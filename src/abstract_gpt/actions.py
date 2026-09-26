"""JSON actions; credentials remain in Codex's own store."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
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
    candidate = Path.home() / ".local/bin/codex"
    found = os.environ.get("AG_CODEX_BIN")
    if not found and candidate.is_file():
        found = str(candidate)
    if not found:
        found = shutil.which("codex")
    if not found:
        raise FileNotFoundError("Install Codex CLI or set AG_CODEX_BIN")
    return found


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
    return {"ok": True, "root": str(root())}


def set_model(default_model=None):
    """Set the wrapper launch default; null clears it. Explicit CLI flags win."""
    if default_model is not None and (not isinstance(default_model, str) or not default_model.strip()):
        raise ValueError("default_model must be a nonempty string or null")
    cfg = read_json(root() / "config.json")
    cfg["default_model"] = default_model
    write(root() / "config.json", json.dumps(cfg, indent=2) + "\n")
    return {"ok": True, "default_model": default_model}


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
        worker = subprocess.Popen([sys.executable, "-m", "abstract_gpt.login_worker", str(lock.fileno())],
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
    cfg = read_json(root() / "config.json")
    command = [binary(), *toolserver_args()]
    if execute:
        command += ["exec", "--ephemeral"]
    if cfg.get("default_model") and not any(
        a in ("-m", "--model") or a.startswith(("--model=", "-m")) for a in args
    ):
        command += ["--model", cfg["default_model"]]
    return subprocess.call(command + args, env=env())
