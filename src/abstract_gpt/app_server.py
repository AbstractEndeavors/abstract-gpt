"""Codex app-server adapter. No credentials or transcript scraping over HTTP.

The line-delimited JSON-RPC transport is isolated here so the console can keep
its own event contract. Tested against the schemas from Codex CLI 0.154.0.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

from . import actions


class RpcClient:
    def __init__(self, command=None, cwd=None, timeout=30):
        self.timeout = timeout
        self.lock = threading.RLock()
        self.pending = {}
        self.events = queue.Queue()
        self.sequence = 0
        self.closed = False
        # stderr must be drained independently; it can fill during a long turn.
        self.proc = subprocess.Popen(command or [actions.binary(), *actions.toolserver_args(), "app-server"],
                                     cwd=cwd, env=actions.env(), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True, name="gpt-rpc").start()
        try:
            self.call("initialize", {"clientInfo": {"name": "abstract_gpt",
                      "title": "Station console", "version": "0.1.3"}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def send(self, obj):
        with self.lock:
            if self.closed:
                raise RuntimeError("Codex app-server disconnected")
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    waiter = self.pending.get(obj.get("id")) if "method" not in obj else None
                if waiter:
                    waiter.put(obj)
                else:
                    self.events.put(obj)
        finally:
            with self.lock:
                self.closed = True
                for waiter in self.pending.values():
                    waiter.put({"error": {"message": "Codex app-server disconnected"}})
            self.events.put({"method": "_disconnected"})

    def call(self, method, params):
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            waiter = queue.Queue()
            self.pending[request_id] = waiter
        try:
            self.send({"id": request_id, "method": method, "params": params})
            try:
                result = waiter.get(timeout=self.timeout)
            except queue.Empty:
                raise TimeoutError(f"Codex {method} timed out") from None
            if "error" in result:
                raise RuntimeError(result["error"].get("message", "Codex request failed"))
            return result.get("result", {})
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                stream.close()
        self.closed = True


class Adapter:
    """One active run per instance. The coordinator owns session serialization."""
    name = "gpt"

    def __init__(self, client_factory=RpcClient):
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.client = None
        self.thread_id = self.turn_id = None
        self.requests = {}
        self.cancelled = threading.Event()

    def interrupt(self):
        self.cancelled.set()
        with self.lock:
            client, thread, turn = self.client, self.thread_id, self.turn_id
        if client and thread and turn:
            client.call("turn/interrupt", {"threadId": thread, "turnId": turn})

    def respond(self, request_id, decision):
        with self.lock:
            request = self.requests.get(str(request_id))
            if not request or not self.client:
                raise ValueError("Approval is no longer pending")
            if decision not in ("accept", "acceptForSession", "decline", "cancel"):
                raise ValueError("Invalid approval decision")
            method = request["method"]
            if method == "item/permissions/requestApproval":
                permissions = request.get("params", {}).get("permissions", {})
                result = {"permissions": permissions if decision.startswith("accept") else {},
                          "scope": "session" if decision == "acceptForSession" else "turn"}
            else:
                result = {"decision": decision}
            self.client.send({"id": request["id"], "result": result})
            self.requests.pop(str(request_id))

    def run(self, session, prompt, emit):
        client = self.client_factory(cwd=session["cwd"])
        with self.lock:
            self.client = client
        try:
            dangerous = bool(session.get("dangerous"))
            params = {"cwd": session["cwd"], "approvalPolicy": "never" if dangerous else "on-request",
                      "sandbox": "danger-full-access" if dangerous else "workspace-write"}
            if session.get("model"):
                params["model"] = session["model"]
            native = session.get("native_id")
            if native:
                params["threadId"] = native
            result = client.call("thread/resume" if native else "thread/start", params)
            self.thread_id = result["thread"]["id"]
            emit({"type": "session", "native_id": self.thread_id,
                  "model": result.get("model") or session.get("model", "")})
            if self.cancelled.is_set():
                raise RuntimeError("Turn interrupted before dispatch")
            params = {"threadId": self.thread_id,
                      "input": [{"type": "text", "text": prompt}]}
            if session.get("effort"):
                params["effort"] = session["effort"]
            turn = client.call("turn/start", params)
            self.turn_id = turn["turn"]["id"]
            if self.cancelled.is_set():
                self.interrupt()
            answers = {}
            deadline = time.monotonic() + 12 * 3600
            while time.monotonic() < deadline:
                try:
                    obj = client.events.get(timeout=1)
                except queue.Empty:
                    continue
                method, p = obj.get("method", ""), obj.get("params", {})
                if method == "_disconnected":
                    raise RuntimeError("Codex app-server disconnected during turn")
                if "id" in obj and method:
                    supported = ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                                 "item/permissions/requestApproval")
                    if method in supported:
                        with self.lock:
                            self.requests[str(obj["id"])] = obj
                        emit({"type": "approval", "request_id": obj["id"], "method": method,
                              "params": p})
                    else:
                        # Never leave an unimplemented request silently waiting.
                        client.send({"id": obj["id"], "error": {"code": -32601,
                                    "message": "Console does not support " + method}})
                        emit({"type": "note", "text": "Unsupported Codex request: " + method})
                elif method == "serverRequest/resolved":
                    with self.lock:
                        self.requests.pop(str(p.get("requestId")), None)
                    emit({"type": "approval_resolved", "request_id": p.get("requestId")})
                elif p.get("threadId") not in (None, self.thread_id):
                    continue
                elif method == "item/agentMessage/delta":
                    emit({"type": "text", "text": p.get("delta", "")})
                elif method == "item/commandExecution/outputDelta":
                    emit({"type": "tool_result", "text": p.get("delta", "")})
                elif method in ("item/started", "item/completed"):
                    item = p.get("item", {})
                    kind = item.get("type")
                    if kind == "agentMessage" and method == "item/completed":
                        answers[item["id"]] = item.get("text", "")
                    elif kind in ("commandExecution", "fileChange", "mcpToolCall", "webSearch"):
                        emit({"type": "tool", "name": kind, "summary": item.get("command") or kind,
                              "item": item, "status": "started" if method.endswith("started") else "completed"})
                elif method == "thread/tokenUsage/updated":
                    emit({"type": "usage", "usage": p.get("tokenUsage", {})})
                elif method == "turn/completed" and p.get("turn", {}).get("id") == self.turn_id:
                    turn = p["turn"]
                    if turn.get("status") != "completed":
                        raise RuntimeError((turn.get("error") or {}).get("message") or
                                           "Turn " + turn.get("status", "failed"))
                    return "\n\n".join(answers.values())
                elif method == "error":
                    emit({"type": "note", "text": (p.get("error") or {}).get("message", "Codex error")})
            raise TimeoutError("Codex turn exceeded 12 hours")
        finally:
            with self.lock:
                unresolved = list(self.requests)
                self.client = None
                self.requests.clear()
            for request_id in unresolved:
                emit({"type": "approval_resolved", "request_id": request_id})
            client.close()
