#!/usr/bin/env bash
# Build a standalone, offline Abstract provider CLI without starting a service.
set -euo pipefail
umask 022
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd -- "$script_dir/.." && pwd -P)
build_python=${BUILD_PYTHON:-/usr/bin/python3}
readarray -t meta < <("$build_python" - "$root/pyproject.toml" <<'PY'
import sys,tomllib
with open(sys.argv[1],'rb') as stream:project=tomllib.load(stream)['project']
print(project['name']);print(project['version'])
PY
)
package=${meta[0]}
version=${meta[1]}
case $package in abstract-gpt|abstract-claude) ;; *) echo "Unsupported provider: $package" >&2; exit 2 ;; esac
out=${1:-"$root/build/debian"}
mkdir -p "$out"
out=$(cd -- "$out" && pwd -P)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
stage=$work/stage
wheels=$stage/usr/share/$package/wheels
mkdir -p "$stage/DEBIAN" "$wheels" "$stage/usr/bin"
chmod 0755 "$stage"
published=${HUGPY_PUBLISHED_WHEELHOUSE:-}
wheel_name=${package//-/_}-${version}-py3-none-any.whl
if [[ -n $published && -f $published/$wheel_name ]]; then
    install -m 0644 "$published/$wheel_name" "$wheels/$wheel_name"
else
    "$build_python" -m pip wheel --disable-pip-version-check --no-deps --wheel-dir "$wheels" "$root"
fi
"$build_python" -m pip download --disable-pip-version-check --only-binary=:all: \
    --dest "$wheels" "$wheels/$wheel_name"
"$build_python" -m venv "$work/verify"
"$work/verify/bin/python" -m pip install --quiet --no-index --find-links "$wheels" "$package==$version"
"$work/verify/bin/python" -m pip check
"$work/verify/bin/$package" --help > "$work/cli-help.txt"
cat > "$stage/DEBIAN/control" <<EOF
Package: $package
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: Abstract Endeavors <support@hugpy.ai>
Depends: python3 (>= 3.11), python3-venv
Recommends: tmux
Description: Abstract provider CLI with an isolated offline Python runtime
 Includes $package and its published Python dependencies. User credentials
 and the external provider CLI remain under the user's control. No service
 is started automatically.
EOF
cat > "$stage/DEBIAN/postinst" <<EOF
#!/bin/sh
set -eu
[ "\${1:-}" = configure ] || exit 0
/usr/bin/python3 -m venv /opt/$package/venv
/opt/$package/venv/bin/python -m pip install --force-reinstall --no-index --find-links=/usr/share/$package/wheels /usr/share/$package/wheels/*.whl
/opt/$package/venv/bin/python -m pip check
EOF
cat > "$stage/DEBIAN/postrm" <<EOF
#!/bin/sh
set -eu
if [ "\${1:-}" = purge ]; then
    /usr/bin/python3 -c 'import shutil; shutil.rmtree("/opt/$package", ignore_errors=True)'
fi
EOF
cat > "$stage/usr/bin/$package" <<EOF
#!/bin/sh
exec /opt/$package/venv/bin/$package "\$@"
EOF
chmod 0755 "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm" "$stage/usr/bin/$package"
artifact=$out/${package}_${version}_all.deb
dpkg-deb --build --root-owner-group "$stage" "$artifact"
sha256sum "$artifact" > "$artifact.sha256"
echo "$artifact"
