"""Toolserver managed Codex terminal rollover."""
import glob
import json
import os
import shlex
import subprocess
import sys
import time

from . import actions
from .comms import _mcp_call


MODES = ("auto", "manual", "off")


def mode():
    """Roller switch: env AG_ROLLOVER_MODE beats <root>/config.json
    rollover_mode; default auto. Re-read every watcher tick so a flip
    takes effect live, no restart."""
    v = (os.environ.get("AG_ROLLOVER_MODE") or "").strip().lower()
    if v in ("on", "1", "true"):
        v = "auto"
    if v in ("0", "false"):
        v = "off"
    if v in MODES:
        return v
    try:
        cfg = actions.read_json(actions.root() / "config.json") or {}
        v = str(cfg.get("rollover_mode") or "auto").strip().lower()
    except Exception:
        v = "auto"
    return v if v in MODES else "auto"


def set_mode(value):
    """Persist rollover_mode into <root>/config.json; on == auto, off/manual literal."""
    value = str(value or "").strip().lower()
    if value in ("on", "1", "true"):
        value = "auto"
    if value in ("0", "false"):
        value = "off"
    if value not in MODES:
        return {"ok": False, "error": "mode must be auto | manual | off (or on/off)"}
    path = actions.root() / "config.json"
    cfg = actions.read_json(path) if path.exists() else {}
    cfg = cfg or {}
    cfg["rollover_mode"] = value
    actions.write(path, json.dumps(cfg, indent=1) + "\n")
    out = {"ok": True, "mode": value}
    env = (os.environ.get("AG_ROLLOVER_MODE") or "").strip().lower()
    if env and env not in ("", value):
        out["warning"] = "env AG_ROLLOVER_MODE=%s overrides config" % env
    return out


def install_hooks():
    path = actions.codex_home() / "hooks.json"
    doc = actions.read_json(path) if path.exists() else {}
    hooks = doc.setdefault("hooks", {})
    command = shlex.quote(sys.executable) + " -m abstract_gpt rollover-hook"
    changed = False
    for event in ("UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop", "Interrupt"):
        groups = hooks.setdefault(event, [])
        found = False
        for group in groups:
            for hook in group.get("hooks", []):
                if "abstract_gpt rollover-hook" in str(hook.get("command", "")):
                    found = True
                    if hook.get("command") != command:
                        hook["command"] = command
                        changed = True
        if not found:
            groups.append({"hooks": [{"type": "command", "command": command, "timeout": 3}]})
            changed = True
    if changed:
        actions.write(path, json.dumps(doc, indent=2) + "\n")


def hook_event():
    event = json.load(sys.stdin)
    sid = str(event.get("session_id") or "").strip()
    kind = event.get("hook_event_name")
    if sid and kind in ("UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop", "Interrupt"):
        folder = actions.root() / "rollover-events"
        actions.private_dir(folder)
        row = {"at": time.time(), "event": kind, "agent_id": event.get("agent_id") or ""}
        fd = os.open(folder / (sid + ".jsonl"), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode())
        finally:
            os.close(fd)
    if kind in ("Stop", "SubagentStop"):
        print("{}")


def idle_state(sid, transcript_mtime):
    path = actions.root() / "rollover-events" / (sid + ".jsonl")
    try:
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return 0, 0
    agents, stopped, last_agent_stop = set(), 0, 0
    for event in events:
        kind, agent = event.get("event"), event.get("agent_id")
        if kind == "SubagentStart" and agent:
            agents.add(agent)
        elif kind == "SubagentStop":
            agents.discard(agent)
            last_agent_stop = max(last_agent_stop, float(event.get("at") or 0))
        elif kind == "Stop":
            stopped = float(event.get("at") or 0)
        elif kind in ("UserPromptSubmit", "Interrupt"):
            stopped = 0
    if not stopped or agents:
        return 0, len(agents)
    return max(0, int(time.time() - max(stopped, last_agent_stop, transcript_mtime))), 0


def tmux_session():
    if not os.environ.get("TMUX"):
        return ""
    try:
        p = subprocess.run(["tmux", "display-message", "-p", "#S"],
                           capture_output=True, text=True, timeout=3)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def send_line(session, line):
    sock = os.environ.get("TMUX", "").split(",", 1)[0]
    prefix = ["tmux", "-S", sock] if sock else ["tmux"]
    target = "=" + session + ":"
    for args in (["send-keys", "-t", target, "-l", "--", line],
                 ["send-keys", "-t", target, "Enter"]):
        p = subprocess.run([*prefix, *args], capture_output=True, timeout=8)
        if p.returncode:
            return False
    return True


def latest_rollout(home):
    paths = glob.glob(os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl"))
    if not paths:
        return "", "", 0, 0, 0, False
    path = max(paths, key=os.path.getmtime)
    st = os.stat(path)
    sid, ctx, window, complete = "", 0, 0, False
    with open(path, "rb") as fh:
        fh.seek(max(0, st.st_size - 512 * 1024))
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            payload = ev.get("payload") or {}
            if ev.get("type") == "session_meta":
                sid = payload.get("id") or sid
            elif payload.get("type") == "token_count":
                info = payload.get("info") or {}
                ctx = int((info.get("last_token_usage") or {}).get("input_tokens") or ctx)
                window = int(info.get("model_context_window") or window)
                complete = False
            elif payload.get("type") == "task_complete":
                complete = True
            elif payload.get("type") in ("task_started", "user_message"):
                complete = False
    if not sid:
        # The session_meta line can lie outside the bounded tail.
        sid = os.path.basename(path)[-42:-6]
    return sid, path, ctx, window, max(0, int(time.time() - st.st_mtime)), complete


def _call(name, args):
    try:
        return _mcp_call(name, args)
    except Exception:
        return {}


def run(watch_pid, locus, session, codex_dir, interval=30):
    started, pinned, handoff_sent, new_sent = time.time(), "", "", ""
    while True:
        try:
            os.kill(watch_pid, 0)
        except ProcessLookupError:
            return 0
        pol = mode()
        sid, path, ctx, window, _quiet, complete = latest_rollout(codex_dir)
        if pol == "off":
            _call("seat_report", {"locus": locus, "seat": "codex", "alive": True,
                "pid": watch_pid, "age_s": int(time.time() - started),
                "session_id": sid or "", "status": {"rollover": {"mode": "off"}}})
        elif sid:
            if not pinned and os.path.getmtime(path) >= started - 30:
                pinned = sid
            if pinned and (sid == pinned or new_sent == pinned):
                idle, active_agents = idle_state(pinned, os.path.getmtime(path))
                threshold = int(window * .85) if window else 250000
                state = _call("seat_rollover", {"locus": locus, "seat": "codex",
                    "session_id": pinned, "context_tokens": ctx, "idle_s": idle if complete else 0,
                    "threshold": threshold, "action": "observe"})
                _call("seat_report", {"locus": locus, "seat": "codex", "alive": True,
                    "pid": watch_pid, "age_s": int(time.time() - started),
                    "session_id": pinned, "status": {"rollover": state, "context_tokens": ctx,
                    "context_window": window, "active_agents": active_agents}})
                phase = state.get("phase")
                if phase == "pending" and pol == "auto" and complete and idle >= 120 and handoff_sent != pinned:
                    prompt = ("Rollover handoff: write the current task state to the toolserver "
                              "ledger for locus " + locus + ". Include goal, rulings, decisions, "
                              "world state, in-flight work, open questions, and pointers. "
                              "Set session_id to " + pinned + ". Stop after reporting the "
                              "locus/task handoff id.")
                    if send_line(session, prompt):
                        handoff_sent = pinned
                if phase == "pending" and pol == "auto" and handoff_sent == pinned:
                    rows = _call("ledger_list", {"locus": locus, "status": "active", "limit": 20})
                    for row in rows if isinstance(rows, list) else []:
                        if (row.get("session_id") == pinned and
                                int(row.get("updated") or 0) >= int(state.get("requested") or 0)):
                            state = _call("seat_rollover", {"locus": locus, "seat": "codex",
                                "session_id": pinned, "action": "handoff",
                                "handoff_id": locus + "/" + row["task"]})
                            break
                if state.get("phase") == "handoff" and pol == "auto" and complete and idle >= 10:
                    if new_sent != pinned and send_line(session, "/clear"):
                        new_sent = pinned
                        time.sleep(4)
                        if send_line(session, "Resume from toolserver ledger " +
                                     state["handoff_id"] + ". Read ledger_get, state the "
                                     "next actions and constraints, then continue the in-flight work."):
                            _call("seat_rollover", {"locus": locus, "seat": "codex",
                                "session_id": pinned, "action": "complete"})
                            pinned = sid if sid != pinned else ""
        time.sleep(max(5, int(interval)))


def spawn(watch_pid, locus=""):
    session = tmux_session()
    locus = locus or os.environ.get("HUGPY_LOCUS") or os.environ.get("EXCHANGE_LOCUS") or ""
    if not session or not locus:
        return False
    argv = [sys.executable, "-m", "abstract_gpt", "rollover-watch", "--pid", str(watch_pid),
            "--locus", locus, "--tmux", session, "--codex-home", str(actions.codex_home())]
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    return True
