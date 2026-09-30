"""Automatic toolserver channel binding for Codex sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys

from . import actions


HOOK_MARKER = "abstract-gpt-comms"


def _mcp_call(name, arguments):
    """Call a toolserver tool through the SHARED client (abstract_toolserver.client;
    endpoint + token via abstract_toolserver.discovery). Raises RuntimeError on
    no token / transport failure / a tool-level {"error"}."""
    from abstract_toolserver.client import ToolserverClient, ToolserverError
    client = ToolserverClient(timeout=30)
    if not client.token:
        raise RuntimeError("no toolserver token found")
    try:
        value = client.call(name, arguments)
    except ToolserverError as exc:
        raise RuntimeError("toolserver call failed: %s" % exc) from exc
    if isinstance(value, dict) and value.get("error"):
        raise RuntimeError(str(value["error"]))
    return value


def _state_path(session_id):
    return actions.root() / "comms" / (str(session_id) + ".json")


def bind_session(session_id, cwd=""):
    """Reuse or create a channel and bind it to one Codex thread."""
    state = actions.read_json(_state_path(session_id))
    channel = state.get("channel_id") or state.get("url")
    if channel:
        try:
            _mcp_call("channel_bind", {"channel": channel, "thread_id": session_id,
                                        "name": state.get("label", "abstract-gpt comms")})
            return state
        except Exception:
            pass
    label = f"abstract-gpt {socket.gethostname()} {cwd or os.getcwd()}"[:200]
    opened = _mcp_call("channel_open", {"label": label})
    _mcp_call("channel_bind", {"channel": opened["channel_id"], "thread_id": session_id,
                                "name": label})
    state = {"session_id": session_id, "channel_id": opened["channel_id"],
             "url": opened["url"], "label": label, "expires_at": opened.get("expires_at", 0)}
    actions.write(_state_path(session_id), json.dumps(state, indent=2) + "\n")
    return state


def session_start_hook(stream=None):
    """Codex SessionStart command hook entry point."""
    event = json.load(stream or sys.stdin)
    session_id = str(event.get("session_id") or "").strip()
    if not session_id:
        raise RuntimeError("SessionStart did not provide session_id")
    state = bind_session(session_id, str(event.get("cwd") or ""))
    url = state["url"]
    print(json.dumps({
        "continue": True,
        "systemMessage": "Toolserver comms channel ready: " + url,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "This Codex session has a bound two-way toolserver comms channel: " + url +
                ". If the user asks for the comms endpoint, provide this exact URL. "
                "Inbound channel posts are queued as pings to this session."
            ),
        },
    }))
    return 0


def current():
    """Return the newest bound channel created by this abstract-gpt installation."""
    directory = actions.root() / "comms"
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if directory.exists() else []
    return actions.read_json(files[0]) if files else {"ok": False, "error": "no bound comms channel yet"}


def ensure_hook():
    """Install the package-owned SessionStart hook idempotently into CODEX_HOME."""
    path = actions.codex_home() / "hooks.json"
    doc = actions.read_json(path) if path.exists() else {}
    hooks = doc.setdefault("hooks", {})
    groups = hooks.setdefault("SessionStart", [])
    command = f'{json.dumps(sys.executable)} -m abstract_gpt comms-hook # {HOOK_MARKER}'
    found = False
    for group in groups:
        for hook in group.get("hooks", []):
            if HOOK_MARKER in str(hook.get("command", "")):
                hook.update({"type": "command", "command": command,
                             "statusMessage": "Binding toolserver comms", "timeout": 45})
                group["matcher"] = "^(startup|resume)$"
                found = True
    if not found:
        groups.append({"matcher": "^(startup|resume)$", "hooks": [{
            "type": "command", "command": command,
            "statusMessage": "Binding toolserver comms", "timeout": 45,
        }]})
    actions.write(path, json.dumps(doc, indent=2) + "\n")
    return path
