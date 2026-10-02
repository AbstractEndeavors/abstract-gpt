"""Command-line entry point."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from . import actions


def main():
    if sys.argv[1:2] == ["rollover-hook"]:
        from .rollover import hook_event
        hook_event()
        return 0
    if sys.argv[1:2] == ["rollover-watch"]:
        from .rollover import run
        parser = argparse.ArgumentParser()
        parser.add_argument("--pid", type=int, required=True)
        parser.add_argument("--locus", required=True)
        parser.add_argument("--tmux", required=True)
        parser.add_argument("--codex-home", required=True)
        a = parser.parse_args(sys.argv[2:])
        return run(a.pid, a.locus, a.tmux, a.codex_home)
    if sys.argv[1:2] == ["rollover-mode"]:
        from .rollover import mode, set_mode
        arg = (sys.argv[2:3] or [""])[0]
        if not arg or arg == "status":
            print(mode())
            return 0
        res = set_mode(arg)
        print(json.dumps(res))
        return 0 if res.get("ok") else 1
    if sys.argv[1:2] == ["rollover"]:
        from .comms import _mcp_call
        locus = sys.argv[2] if len(sys.argv) > 2 else (
            os.environ.get("HUGPY_LOCUS") or os.environ.get("EXCHANGE_LOCUS") or "")
        if not locus:
            print("rollover needs a locus argument or HUGPY_LOCUS", file=sys.stderr)
            return 2
        result = _mcp_call("seat_rollover", {"locus": locus, "seat": "codex",
                                                 "action": "request"})
        print(json.dumps(result, indent=2))
        return 0
    if sys.argv[1:2] == ["mct"]:
        from .mct_repl import main as mct_main
        try:
            return mct_main(sys.argv[2:])
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
    if sys.argv[1:2] in (["launch"], ["exec"]):
        command = sys.argv[1]
        args = sys.argv[2:]
        if args[:1] == ["--"]:
            args = args[1:]
        try:
            return actions.launch(args, execute=command == "exec")
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description="Codex login and session manager")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "state", "oauth-status", "oauth-solution", "login-start",
                 "login-poll", "save-template", "restore", "mcp", "comms", "comms-hook"):
        sub.add_parser(name)
    sub.add_parser("mct", help="MCT pointer-exchange chat using Codex")
    login = sub.add_parser("login")
    methods = login.add_mutually_exclusive_group()
    methods.add_argument("--browser", action="store_true")
    methods.add_argument("--with-api-key", action="store_true")
    sub.add_parser("set-model").add_argument("model", nargs="?")
    sub.add_parser("set-comms").add_argument("state", choices=("on", "off"))
    serve = sub.add_parser("serve", help="Run the shared provider web console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=None,
                       help="default 9124 (the shared console)")
    serve.add_argument("--state", default="~/.local/state/abstract-gpt-serve")
    serve.add_argument("--workspace", default=None)
    serve.add_argument("--no-browser", action="store_true",
                       help="do not open the console URL automatically")
    for name in ("launch", "exec", "rollover"):
        sub.add_parser(name)
    opts = parser.parse_args()
    try:
        if opts.command == "serve":
            # `serve` IS abstract-serve-core's one Serve console, the same
            # program as abstract-claude serve and hugpy-agent serve: it offers
            # the models of every installed provider (abstract-gpt registers
            # GPT via its abstract_serve_core.providers entry point).
            try:
                from abstract_serve_core.serve_cli import main as serve_main
            except ImportError as exc:
                parser.error("abstract-gpt serve needs abstract-serve-core (" + str(exc) + ")")
            # 9124 joins this user's shared console (serve_cli reuses a live
            # one) instead of starting a second copy.
            args = ["--host", opts.host, "--port", str(opts.port or 9124)]
            if opts.no_browser:
                args.append("--no-browser")
            os.environ["AC_SERVE_BACKEND"] = "gpt"
            return serve_main(args)
        if opts.command == "login":
            flags = ["--with-api-key"] if opts.with_api_key else ([] if opts.browser else ["--device-auth"])
            return subprocess.call([actions.binary(), "login"] + flags, env=actions.env(), cwd=Path.home())
        if opts.command == "mcp":
            try:
                from abstract_serve_core.mcp import serve_mcp as bridge
            except ImportError:
                parser.error("abstract-serve-core is required for the toolserver MCP bridge")
            return bridge() or 0
        if opts.command == "comms-hook":
            from .comms import session_start_hook
            return session_start_hook()
        if opts.command == "comms":
            from .comms import current
            result = current()
        elif opts.command == "set-comms":
            result = actions.set_comms(opts.state == "on")
        elif opts.command == "set-model":
            result = actions.set_model(opts.model)
        elif opts.command not in ("comms", "set-comms"):
            names = {"init": "init_root", "state": "build_state", "oauth-status": "auth_status",
                     "oauth-solution": "auth_solution"}
            result = getattr(actions, names.get(opts.command, opts.command.replace("-", "_")))()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok", True) else 1
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
