"""Command-line interface: `abstract-gpt <subcommand>`.

Mirrors abstract-claude's subcommand surface with GPT/Codex-specific provider
delegation. Shared infrastructure (serve, mcp, seat-report, mct, tokens,
keepalive) delegates to abstract-serve-core; GPT-specific commands (login,
launch, rollover) use this package's own modules.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, actions


def _p(obj):
    if isinstance(obj, dict):
        print(obj.get("out") or obj.get("err") or json.dumps(obj))
    else:
        print(obj)


def cmd_serve(a):
    from abstract_serve_core.serve_cli import main as serve_main
    argv = ["--host", a.host or "127.0.0.1",
            "--port", str(a.port or 9124)]
    if a.no_browser:
        argv.append("--no-browser")
    os.environ["AC_SERVE_BACKEND"] = "gpt"
    return serve_main(argv)


def cmd_init(a):
    _p(actions.init_root())
    return cmd_install_cli(a)


def cmd_install_cli(a):
    """Make sure this user has a working Codex (installs ~/.local/bin/codex)."""
    from abstract_serve_core.clis import CliInstallError, ensure_codex, find
    had = find("codex", "AG_CODEX_BIN")
    try:
        path = ensure_codex()
    except CliInstallError as exc:
        print(f"Codex: install failed — {exc}")
        return 1
    print(f"Codex: {'found' if had else 'installed'} {path}")
    return 0


def cmd_save_template(a):
    _p(actions.save_template())


def cmd_restore(a):
    _p(actions.restore())


def cmd_oauth_status(a):
    _p(actions.auth_status())


def cmd_oauth_solution(a):
    _p(actions.auth_solution())


def cmd_login(a):
    flags = (["--with-api-key"] if a.with_api_key
             else [] if a.browser else ["--device-auth"])
    return subprocess.call([actions.binary(), "login"] + flags,
                           env=actions.env(), cwd=Path.home())


def cmd_login_start(a):
    _p(actions.login_start())


def cmd_login_poll(a):
    _p(actions.login_poll())


def cmd_set_model(a):
    _p(actions.set_model(a.model))


def cmd_set_comms(a):
    _p(actions.set_comms(a.state == "on"))


def cmd_state(a):
    _p(actions.build_state())


def cmd_launch(a):
    try:
        return actions.launch(a.args, execute=False)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


def cmd_exec(a):
    try:
        return actions.launch(a.args, execute=True)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


def cmd_seat_report(a):
    from .seat_report import run
    return run(a.seat, a.watch_pid, status_file=a.status_file or "",
               locus=a.locus or "", model=a.model or "",
               interval=a.interval, once=a.once)


def cmd_mcp(a):
    from abstract_serve_core.mcp import serve_mcp
    serve_mcp()


def cmd_mct(a):
    from .mct_repl import main as mct_main
    try:
        return mct_main(sys.argv[2:])
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


def cmd_mct_backfill(a):
    from .mct_archive import backfill_exchanges
    r = backfill_exchanges(a.dir, a.locus, base=a.base)
    _p(r)
    return 0 if not r.get("failed") else 1


def cmd_tokens(a):
    from . import token_test
    return token_test.run(a)


def cmd_keepalive(a):
    from . import keepalive
    return keepalive.run(a)


def cmd_rollover(a):
    from .comms import _mcp_call
    locus = a.locus or os.environ.get("HUGPY_LOCUS") or os.environ.get("EXCHANGE_LOCUS") or ""
    if not locus:
        print("rollover needs --locus or HUGPY_LOCUS", file=sys.stderr)
        return 2
    result = _mcp_call("seat_rollover", {"locus": locus, "seat": "codex",
                                         "action": "request"})
    print(json.dumps(result, indent=2))
    return 0


def cmd_rollover_mode(a):
    from .rollover import mode, set_mode
    if not a.value or a.value == "status":
        print(mode())
        return 0
    r = set_mode(a.value)
    print(json.dumps(r))
    return 0 if r.get("ok") else 1


def cmd_comms(a):
    from .comms import current
    _p(current())


def cmd_comms_hook(a):
    from .comms import session_start_hook
    return session_start_hook()


def cmd_hub(a):
    from . import hub
    hub.serve(host=a.host, port=a.port)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Fast-path subcommands that bypass argparse (rollover hooks are latency-sensitive)
    if argv[:1] == ["rollover-hook"]:
        from .rollover import hook_event
        hook_event()
        return 0
    if argv[:1] == ["rollover-watch"]:
        from .rollover import run as rollover_run
        p = argparse.ArgumentParser()
        p.add_argument("--pid", type=int, required=True)
        p.add_argument("--locus", required=True)
        p.add_argument("--tmux", required=True)
        p.add_argument("--codex-home", required=True)
        a = p.parse_args(argv[1:])
        return rollover_run(a.pid, a.locus, a.tmux, a.codex_home)
    # launch/exec forward their args to codex verbatim and must run BEFORE argparse:
    # a REMAINDER subparser intercepts a leading option (e.g. --yolo) as unknown.
    if argv[:1] in (["launch"], ["exec"]):
        rest = argv[1:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        try:
            return actions.launch(rest, execute=argv[0] == "exec")
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1

    ap = argparse.ArgumentParser(prog="abstract-gpt",
                                 description="Codex session manager.")
    ap.add_argument("-V", "--version", action="version",
                    version=f"abstract-gpt {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # --- serve (shared console via abstract-serve-core) ---
    s = sub.add_parser("serve", help="run the web console")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_serve)

    h = sub.add_parser("hub", help="run the fleet hub")
    h.add_argument("--host"); h.add_argument("--port", type=int)
    h.set_defaults(fn=cmd_hub)

    # --- Codex lifecycle ---
    sub.add_parser("init", help="create private wrapper storage and install Codex if missing").set_defaults(fn=cmd_init)
    sub.add_parser("install-cli", help="install this user's own Codex (~/.local/bin/codex) if missing").set_defaults(fn=cmd_install_cli)
    sub.add_parser("state", help="show wrapper config and auth status").set_defaults(fn=cmd_state)
    sub.add_parser("save-template", help="save config.toml as template").set_defaults(fn=cmd_save_template)
    sub.add_parser("restore", help="restore saved config if missing").set_defaults(fn=cmd_restore)

    # --- auth ---
    sub.add_parser("oauth-status", help="show login status").set_defaults(fn=cmd_oauth_status)
    sub.add_parser("oauth-solution", help="describe login paths").set_defaults(fn=cmd_oauth_solution)
    lg = sub.add_parser("login", help="interactive Codex login")
    lgm = lg.add_mutually_exclusive_group()
    lgm.add_argument("--browser", action="store_true")
    lgm.add_argument("--with-api-key", action="store_true")
    lg.set_defaults(fn=cmd_login)
    sub.add_parser("login-start", help="start device login flow").set_defaults(fn=cmd_login_start)
    sub.add_parser("login-poll", help="poll device login status").set_defaults(fn=cmd_login_poll)

    # --- model / comms ---
    sm = sub.add_parser("set-model", help="set default model")
    sm.add_argument("model", nargs="?")
    sm.set_defaults(fn=cmd_set_model)

    sc = sub.add_parser("set-comms", help="toggle toolserver comms")
    sc.add_argument("state", choices=("on", "off"))
    sc.set_defaults(fn=cmd_set_comms)
    sub.add_parser("comms", help="show bound comms channel").set_defaults(fn=cmd_comms)
    sub.add_parser("comms-hook", help=argparse.SUPPRESS).set_defaults(fn=cmd_comms_hook)

    # --- launch / exec ---
    l = sub.add_parser("launch", help="launch Codex with toolserver + rollover hooks")
    l.add_argument("args", nargs=argparse.REMAINDER)
    l.set_defaults(fn=cmd_launch)
    e = sub.add_parser("exec", help="ephemeral Codex exec")
    e.add_argument("args", nargs=argparse.REMAINDER)
    e.set_defaults(fn=cmd_exec)

    # --- seat-report (shared, delegates to serve-core) ---
    sr = sub.add_parser("seat-report", help="report seat state to the toolserver")
    sr.add_argument("--seat", required=True,
                    choices=["mct", "claude-code", "codex", "local", "shell", "keeper"])
    sr.add_argument("--watch-pid", type=int, required=True)
    for flag in ("--status-file", "--locus", "--model", "--config-dir", "--tmux", "--session-id"):
        sr.add_argument(flag, default="")
    sr.add_argument("--interval", type=int, default=30)
    sr.add_argument("--once", action="store_true")
    sr.set_defaults(fn=cmd_seat_report)

    # --- mcp (shared bridge to toolserver) ---
    sub.add_parser("mcp", help="stdio MCP bridge to the toolserver").set_defaults(fn=cmd_mcp)

    # --- mct (GPT-specific REPL) ---
    sub.add_parser("mct", help="MCT pointer-exchange REPL using Codex").set_defaults(fn=cmd_mct)

    mb = sub.add_parser("mct-backfill", help="ingest turn-*.md files into exchanges DB")
    mb.add_argument("dir")
    mb.add_argument("--locus", required=True)
    mb.add_argument("--base", default=None)
    mb.set_defaults(fn=cmd_mct_backfill)

    # --- tokens / keepalive (shared, from serve-core) ---
    from .usage_db import DEFAULT_DB
    t = sub.add_parser("tokens", help="headless prompt with token/cost logging")
    t.add_argument("prompt", nargs="?")
    t.add_argument("--db", default=DEFAULT_DB)
    t.add_argument("--model", default=None)
    t.add_argument("--tools", default=None)
    t.add_argument("--permission-mode", dest="permission_mode", default="acceptEdits")
    t.add_argument("--max-turns", dest="max_turns", type=int, default=None)
    t.add_argument("--cwd", default=None)
    t.add_argument("--resume", default=None, metavar="SESSION_ID")
    t.add_argument("--continue-last", dest="continue_last", action="store_true")
    t.add_argument("--tag", default=None)
    t.add_argument("--report", action="store_true")
    t.add_argument("--last", type=int, default=5)
    t.add_argument("--cache", action="store_true")
    t.add_argument("--all", action="store_true")
    t.set_defaults(fn=cmd_tokens)

    from .keepalive import KEEPALIVE_DIR, DEFAULT_PING, DEFAULT_LEAD_S, DEFAULT_INTERVAL_S
    k = sub.add_parser("keepalive",
                        help=f"keep sessions' prompt caches warm (dir: {KEEPALIVE_DIR})")
    k.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    k.add_argument("--lead", type=int, default=DEFAULT_LEAD_S)
    k.add_argument("--ping", default=DEFAULT_PING)
    k.add_argument("--once", action="store_true")
    k.add_argument("--add", metavar="SESSION_ID")
    k.add_argument("--rm", metavar="SESSION_ID")
    k.add_argument("--list", action="store_true")
    k.add_argument("--model")
    k.add_argument("--config-dir", dest="config_dir")
    k.add_argument("--cwd")
    k.set_defaults(fn=cmd_keepalive)

    # --- rollover (GPT-specific) ---
    ro = sub.add_parser("rollover", help="request a managed rollover")
    ro.add_argument("--locus", default="")
    ro.set_defaults(fn=cmd_rollover)

    rm = sub.add_parser("rollover-mode", help="get/set rollover mode (auto|manual|off)")
    rm.add_argument("value", nargs="?", default="status")
    rm.set_defaults(fn=cmd_rollover_mode)

    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
