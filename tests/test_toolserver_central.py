"""abstract-gpt over the CENTRAL abstract-toolserver (2026-09-30): `abstract-gpt mcp`
is the shared bridge (+ serve relay), comms tool calls go through the shared
client, and the endpoint resolves via abstract_toolserver.discovery."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from abstract_toolserver import client as C  # noqa: E402
from abstract_toolserver import discovery as D  # noqa: E402
from abstract_toolserver import mcp as bridge  # noqa: E402


class _Resp:
    def __init__(self, doc):
        self._b = json.dumps(doc).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _advertise(monkeypatch, tmp_path, port, token):
    monkeypatch.setattr(D, "SYSTEM_DIRS", ())
    for k in D.URL_ENV_KEYS + D.TOKEN_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("HUGPY_HOME", raising=False)
    tf = tmp_path / "ts.env"
    tf.write_text("TOOLSERVER_OPERATOR_TOKEN=%s\n" % token)
    D.write_advertisement(D.make_advertisement("http://127.0.0.1:%d" % port, "127.0.0.1", port,
                                               os.getpid(), token_file=str(tf)))


def test_comms_call_resolves_via_discovery_and_uses_shared_client(monkeypatch, tmp_path):
    _advertise(monkeypatch, tmp_path, 7989, "tok-gpt")
    from abstract_gpt import comms
    seen = []

    def fake_open(req, timeout=None):
        seen.append((req.full_url, req.get_header("X-operator-token"), json.loads(req.data)))
        return _Resp({"result": {"channel_id": "ch_x"}})
    monkeypatch.setattr(C.urllib.request, "urlopen", fake_open)
    assert comms._mcp_call("channel_open", {"label": "t"}) == {"channel_id": "ch_x"}
    assert seen == [("http://127.0.0.1:7989/ts/call", "tok-gpt",
                     {"name": "channel_open", "arguments": {"label": "t"}})]


def test_mcp_command_is_the_shared_bridge_with_relay(monkeypatch):
    from abstract_gpt import cli
    ran = {}
    monkeypatch.setattr(bridge, "EXTRA_TOOLS", {})
    monkeypatch.setattr(bridge, "serve_mcp", lambda: ran.setdefault("bridge", True))
    monkeypatch.setattr(sys, "argv", ["abstract-gpt", "mcp"])
    assert cli.main() == 0
    assert ran == {"bridge": True} and "session_message" in bridge.EXTRA_TOOLS


def test_first_run_hook_uses_existing_endpoint(monkeypatch, tmp_path):
    _advertise(monkeypatch, tmp_path, 7988, "t")
    from abstract_gpt import actions
    res = actions.ensure_toolserver(wait=0)
    assert res["url"] == "http://127.0.0.1:7988" and res["source"] == "advertised"
