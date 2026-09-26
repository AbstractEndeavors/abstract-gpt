"""Command-line entry point."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from . import actions


def main():
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
                 "login-poll", "save-template", "restore", "mcp"):
        sub.add_parser(name)
    sub.add_parser("mct", help="MCT pointer-exchange chat using Codex")
    login = sub.add_parser("login")
    methods = login.add_mutually_exclusive_group()
    methods.add_argument("--browser", action="store_true")
    methods.add_argument("--with-api-key", action="store_true")
    sub.add_parser("set-model").add_argument("model", nargs="?")
    serve = sub.add_parser("serve", help="Run the shared Claude/GPT console (requires the serve extra)")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    for name in ("launch", "exec"):
        sub.add_parser(name)
    opts = parser.parse_args()
    try:
        if opts.command == "serve":
            try:
                from abstract_claude.server import serve
                from abstract_claude import console_service
            except ImportError:
                parser.error("Install abstract-gpt[serve] for the shared console")
            return serve(host=opts.host, port=opts.port) or 0
        if opts.command == "login":
            flags = ["--with-api-key"] if opts.with_api_key else ([] if opts.browser else ["--device-auth"])
            return subprocess.call([actions.binary(), "login"] + flags, env=actions.env(), cwd=Path.home())
        if opts.command == "mcp":
            try:
                from abstract_claude.mcp import serve_mcp as bridge
            except ImportError:
                parser.error("Install abstract-gpt[mcp] to use the toolserver MCP bridge")
            return bridge() or 0
        if opts.command == "set-model":
            result = actions.set_model(opts.model)
        else:
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
