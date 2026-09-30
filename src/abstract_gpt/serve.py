"""Standalone GPT web service, sharing the durable console protocol, not its listener."""
import hmac
import json
import os
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def handler(coordinator, workspace, token=""):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value, content_type="application/json"):
            data = json.dumps(value).encode() if content_type == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if token:
                return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return (urlsplit("http://" + host).hostname in ("localhost", "127.0.0.1", "::1")
                    and (not origin or urlsplit(origin).netloc == host))

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def dispatch(self, post):
            try:
                url = urlsplit(self.path)
                if not post and url.path in ("/", "/index.html"):
                    return self.reply(200, Path(__file__).with_name("serve.html").read_bytes(), "text/html; charset=utf-8")
                if not self.authorized():
                    return self.reply(401, {"error": "Service authentication required"})
                body = {}
                if post:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 131072:
                        raise ValueError("Invalid request body length")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("Expected a JSON object")
                query = parse_qs(url.query)
                sid = body.get("session_id") or query.get("id", [""])[0]
                route = url.path
                if not post and route == "/api/console/models":
                    from .models import catalog
                    return self.reply(200, catalog())
                if not post and route in ("/api/state", "/api/console/sessions"):
                    return self.reply(200, {"backend": "gpt", "backends": ["gpt"],
                                           "cwd": str(workspace), "sessions": coordinator.sessions()})
                if post and route == "/api/console/sessions":
                    if body.get("backend", "gpt") != "gpt":
                        raise ValueError("This is the GPT service")
                    config = {k: body[k] for k in ("model", "effort", "label") if k in body}
                    return self.reply(201, coordinator.create(backend="gpt", cwd=str(workspace), **config))
                if not coordinator.get(sid):
                    return self.reply(404, {"error": "Unknown session"})
                if post and route == "/api/console/switch":
                    if body.get("backend", "gpt") != "gpt":
                        raise ValueError("This is the GPT service")
                    config = {k: body[k] for k in ("model", "effort") if k in body}
                    return self.reply(200, coordinator.switch_backend(sid, "gpt", **config))
                if not post and route == "/api/console/events":
                    return self.reply(200, {"events": coordinator.events(sid, query.get("since", [0])[0]),
                                           "session": coordinator.get(sid), "queue": coordinator.queue(sid)})
                if post and route == "/api/console/chat":
                    return self.reply(202, coordinator.submit(sid, body.get("prompt")))
                if post and route == "/api/console/interrupt":
                    coordinator.interrupt(sid)
                    return self.reply(200, {"ok": True})
                if post and route == "/api/console/approval":
                    coordinator.approval(sid, body.get("request_id"), body.get("decision"))
                    return self.reply(200, {"ok": True})
                if post and route == "/api/console/queue":
                    return self.reply(200, coordinator.edit_queue(sid, body.get("action"), body))
                self.reply(404, {"error": "Unknown route"})
            except (ValueError, TypeError) as exc:
                self.reply(400, {"error": str(exc)})
            except RuntimeError as exc:
                self.reply(409, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                logging.exception("GPT service request failed")
                self.reply(500, {"error": "Internal service error"})
    return Handler


def serve(host="127.0.0.1", port=9127, state="~/.local/state/abstract-gpt-serve", workspace=None):
    from abstract_claude.console_service import Coordinator
    from .app_server import Adapter
    token = os.environ.get("AG_SERVE_TOKEN", "")
    if host not in ("localhost", "127.0.0.1") and not token:
        raise ValueError("AG_SERVE_TOKEN is required when listening beyond loopback")
    workspace = Path(workspace or os.getcwd()).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("Workspace directory does not exist")
    os.umask(0o077)
    state = Path(state).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    coordinator = Coordinator(state / "console.sqlite3", compiler=lambda fragments: ("\n".join(fragments), "direct"),
                              factory=lambda backend: Adapter())
    previous_signals = {}
    try:
        server = ThreadingHTTPServer((host, port), handler(coordinator, workspace, token))
        try:
            if threading.current_thread() is threading.main_thread():
                def stop(signum, frame):
                    threading.Thread(target=server.shutdown, daemon=True).start()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous_signals[sig] = signal.signal(sig, stop)
            print(f"abstract-gpt serve: http://{host}:{server.server_port}", flush=True)
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        for sig, previous in previous_signals.items():
            signal.signal(sig, previous)
        coordinator.close()
