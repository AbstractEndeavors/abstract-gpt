"""JSON actions; credentials remain in Codex's own store."""
import fcntl
import json
import os
import shutil
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import threading


def root():
    return Path(os.environ.get("AG_ROOT", "~/.local/share/abstract_gpt")).expanduser()


def codex_home():
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def binary():
    """This user's Codex: the AG_CODEX_BIN pin, else ~/.local/bin/codex, else PATH.
    When there is none, OpenAI's release binary is installed into ~/.local/bin/codex
    (abstract_serve_core.processes.clis) instead of failing."""
    candidate = Path.home() / ".local/bin/codex"
    found = os.environ.get("AG_CODEX_BIN")
    if not found and candidate.is_file():
        found = str(candidate)
    if not found:
        found = shutil.which("codex")
    if not found:
        from abstract_serve_core.processes.clis import CliInstallError, install_codex
        try:
            found = install_codex()
        except CliInstallError as exc:
            raise FileNotFoundError(f"Codex CLI is missing and could not be installed: {exc}") from exc
    return found
    from abstract_serve_core.processes.clis import CliInstallError, ensure_codex
    try:
        _BINARY = ensure_codex()
    except CliInstallError as exc:
        raise FileNotFoundError(f"Codex CLI is missing and could not be installed: {exc}") from exc
    return _BINARY


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write(path, text):
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def env():
    result = os.environ.copy()
    result["CODEX_HOME"] = str(codex_home())
    return result


def ensure_toolserver(wait=20.0):
    """First-run hook (2026-09-30): the ONE toolserver for this host via
    abstract_toolserver.discovery.ensure_endpoint (configured URL, else the local
    advertisement; a local instance is started only when the server extra is
    importable). Non-fatal; returns the ensure_endpoint dict or {"error": ...}."""
    try:
        import importlib.util as _iu
        from abstract_toolserver import discovery as _d
        return _d.ensure_endpoint(start=_iu.find_spec("abstract_flask") is not None, wait=wait)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def toolserver_args():
    """Expose the bundled stdio bridge on every Codex launch, independent of CODEX_HOME."""
    return ["-c", f"mcp_servers.toolserver.command={json.dumps(sys.executable)}",
            "-c", 'mcp_servers.toolserver.args=["-m","abstract_gpt","mcp"]']


def auth_status():
    """Check local login status without returning credentials or account data."""
    try:
        result = subprocess.run([binary(), "login", "status"], env=env(),
                                capture_output=True, text=True, timeout=20, cwd=Path.home())
        return {"ok": True, "authenticated": result.returncode == 0,
                "codex_home": str(codex_home())}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "authenticated": False, "error": str(exc)}


def build_state():
    """Show wrapper configuration and local authentication status, never tokens."""
    return {"root": str(root()), "auth": auth_status(),
            "config": read_json(root() / "config.json")}


def init_root():
    """Create private wrapper storage; leave existing Codex state intact."""
    private_dir(root())
    if not (root() / "config.json").exists():
        write(root() / "config.json", '{}\n')
    skills = install_skills()
    return {"ok": True, "root": str(root()), "skills": skills}


def install_skills():
    """Install packaged toolserver skills into this Codex profile, add-only.

    Skills are kept in CODEX_HOME so custom Codex profiles get the same
    abstract-gpt workflows. Never overwrite an existing user skill.
    """
    try:
        from importlib.resources import files
        source = files("abstract_gpt") / "templates" / "skills"
        if not source.is_dir():
            return {"installed": [], "preserved": []}
        installed, preserved = [], []
        skill_root = codex_home() / "skills"
        for entry in source.iterdir():
            manifest = entry / "SKILL.md"
            if not entry.is_dir() or not manifest.is_file():
                continue
            destination = skill_root / entry.name
            target = destination / "SKILL.md"
            if target.exists():
                preserved.append(entry.name)
                continue
            destination.mkdir(parents=True, exist_ok=True)
            # Exclusive create keeps a concurrent/user-created file intact.
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                preserved.append(entry.name)
                continue
            with os.fdopen(fd, "w") as stream:
                stream.write(manifest.read_text())
            installed.append(entry.name)
        return {"installed": installed, "preserved": preserved}
    except Exception as exc:
        return {"error": str(exc), "installed": [], "preserved": []}


def set_model(default_model=None):
    """Set the wrapper launch default; null clears it. Explicit CLI flags win."""
    if default_model is not None and (not isinstance(default_model, str) or not default_model.strip()):
        raise ValueError("default_model must be a nonempty string or null")
    cfg = read_json(root() / "config.json")
    cfg["default_model"] = default_model
    write(root() / "config.json", json.dumps(cfg, indent=2) + "\n")
    return {"ok": True, "default_model": default_model}


def set_comms(enabled=True):
    cfg = read_json(root() / "config.json")
    cfg["comms_enabled"] = bool(enabled)
    write(root() / "config.json", json.dumps(cfg, indent=2) + "\n")
    return {"ok": True, "comms_enabled": bool(enabled)}


def save_template():
    """Save config.toml only, privately; auth and transcripts are never copied."""
    source = codex_home() / "config.toml"
    if not source.is_file():
        return {"ok": False, "error": "No Codex config.toml to save"}
    write(root() / "template/config.toml", source.read_text())
    return {"ok": True}


def restore():
    """Restore saved config only when no live config exists."""
    target = codex_home() / "config.toml"
    source = root() / "template/config.toml"
    if target.exists():
        return {"ok": True, "changed": False}
    if not source.exists():
        return {"ok": False, "error": "No saved template"}
    private_dir(target.parent)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {"ok": True, "changed": False}
    with os.fdopen(fd, "w") as stream:
        stream.write(source.read_text())
    return {"ok": True, "changed": True}


def auth_solution():
    """Describe supported login paths for this service's own OS account."""
    return {"status": auth_status(), "steps": [
        "POST /gpt/login_start, then poll /gpt/login_poll for the URL and one-time code.",
        "Approve in your browser. Enable device login in ChatGPT security settings first.",
        "Alternatively run abstract-gpt login --browser as the service OS user.",
        "For API billing: pipe a key to abstract-gpt login --with-api-key."],
        "documentation": "https://developers.openai.com/codex/auth/"}


def login_start():
    """Start device login, or return the current attempt shared across workers."""
    init_root()
    lock = open(root() / "login.lock", "a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return login_poll()
        status = auth_status()
        if not status["ok"]:
            return status
        if status["authenticated"]:
            return {"ok": True, "state": "authenticated"}
        write(root() / "login-output.txt", "")
        write(root() / "login.json", json.dumps({"state": "pending", "started": time.time()}))
        worker = subprocess.Popen([sys.executable, "-m", "abstract_gpt.configuration.login_worker", str(lock.fileno())],
                         env=env(), pass_fds=(lock.fileno(),), start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        threading.Thread(target=worker.wait, daemon=True).start()
        return {"ok": True, "state": "pending", "poll": "/gpt/login_poll"}
    finally:
        lock.close()


def login_poll():
    """Return only device URL/code and completion state, never raw CLI output."""
    state = read_json(root() / "login.json") or {"state": "idle"}
    if state["state"] == "pending":
        with open(root() / "login.lock", "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state["state"] = "interrupted"
            except BlockingIOError:
                pass
    if state["state"] == "pending":
        try:
            with open(root() / "login-output.txt") as stream:
                output = stream.read(16384)
        except FileNotFoundError:
            output = ""
        output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
        url = re.search(r"https://(?:auth\.openai\.com|chatgpt\.com)/[\w/.-]*device[\w/.-]*", output)
        code = re.search(r"\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b", output)
        if url and code:
            state.update(verification_url=url.group(), user_code=code.group())
    return {"ok": state["state"] not in ("failed", "expired", "interrupted"), **state}


def launch(args, execute=False):
    """New conversation, same refreshable credential store; exec is ephemeral."""
    install_skills()
    cfg = read_json(root() / "config.json")
    comms_enabled = cfg.get("comms_enabled", True)
    if comms_enabled:
        from ..integrations.comms import ensure_hook
        ensure_hook()
    from ..processes.rollover import install_hooks
    install_hooks()
    ensure_toolserver()
    command = [binary(), *toolserver_args()]
    if "--dangerously-bypass-hook-trust" not in args:
        command += ["--dangerously-bypass-hook-trust"]
    if execute:
        command += ["exec", "--ephemeral"]
    if cfg.get("default_model") and not any(
        a in ("-m", "--model") or a.startswith(("--model=", "-m")) for a in args
    ):
        command += ["--model", cfg["default_model"]]
    if execute:
        return subprocess.call(command + args, env=env())
    proc = subprocess.Popen(command + args, env=env())
    try:
        from ..processes.rollover import spawn
        spawn(proc.pid)
    except Exception:
        pass
    return proc.wait()
