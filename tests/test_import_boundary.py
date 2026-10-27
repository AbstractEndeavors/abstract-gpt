"""The import facade must work without any feature-specific dependencies."""
import subprocess
import sys


def test_stdlib_and_constants_do_not_load_feature_dependencies():
    code = r'''
import importlib.abc
import sys

class BlockOptional(importlib.abc.MetaPathFinder):
    blocked = {'abstract_serve_core', 'abstract_toolserver', 'prompt_toolkit'}
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in self.blocked:
            raise ModuleNotFoundError('blocked optional dependency: ' + fullname, name=fullname)

sys.meta_path.insert(0, BlockOptional())
from abstract_gpt import imports
from abstract_gpt.imports import init_imports as boundary
from abstract_gpt.imports import json, Path, _iu, _u, _v, MODES
from abstract_gpt.integrations.app_server import OUTPUT_CAP
assert json.loads('{}') == {}
assert Path('example').name == 'example'
assert _iu.__name__ == 'importlib.util'
assert _u.__name__ == 'urllib.request'
assert callable(_v)
assert MODES == ('auto', 'manual', 'off')
assert OUTPUT_CAP == 64 * 1024
assert boundary.json is imports.json is json
assert not (BlockOptional.blocked & sys.modules.keys())
for name in ('token_test', 'ToolserverClient', 'PromptSession'):
    try:
        getattr(imports, name)
    except ModuleNotFoundError as exc:
        assert exc.name.split('.')[0] in BlockOptional.blocked
    else:
        raise AssertionError(name + ' did not report the missing dependency')
assert imports.json is json
try:
    imports.not_a_declared_import
except AttributeError:
    pass
else:
    raise AssertionError('unknown name should raise AttributeError')
'''
    subprocess.run([sys.executable, '-c', code], check=True, timeout=20)


def test_backend_alias_resolves_only_its_owner_and_is_cached(monkeypatch):
    import types
    from abstract_gpt.imports import init_imports as boundary
    from hugpy_resources import lazy_imports

    marker = object()
    calls = []

    def import_owner(module, package=None):
        calls.append(module)
        assert module == 'abstract_serve_core.serve_cli'
        return types.SimpleNamespace(main=marker)

    monkeypatch.setattr(lazy_imports, 'import_module', import_owner)
    monkeypatch.delitem(boundary.__dict__, 'serve_main', raising=False)
    try:
        assert boundary.serve_main is marker
        assert boundary.serve_main is marker
        assert calls == ['abstract_serve_core.serve_cli']
    finally:
        boundary.__dict__.pop('serve_main', None)
