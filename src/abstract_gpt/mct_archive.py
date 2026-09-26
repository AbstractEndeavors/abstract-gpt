"""Retryable MCT archive, adapted from abstract_claude; see MCT-LICENSE.txt."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import threading
import time
import urllib.request


def _atomic(path, data):
    tmp = path.with_name(path.name + ".tmp-" + str(threading.get_ident()))
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Archive:
    def __init__(self, ws, locus=None):
        self.ws = Path(ws).expanduser().resolve()
        self.root = self.ws / "archive"
        self.root.mkdir(parents=True, exist_ok=True)
        self.locus = locus or os.environ.get("MCT_LOCUS") or os.environ.get("EXCHANGE_LOCUS")
        self.base = (os.environ.get("EXCHANGE_INGEST_URL") or
                     os.environ.get("TOOLSERVER_URL") or "https://toolserver.hugpy.ai").rstrip("/")
        self.token = (os.environ.get("TOOLSERVER_TOKEN") or
                      os.environ.get("TOOLSERVER_OPERATOR_TOKEN") or
                      os.environ.get("HUGPY_OPERATOR_TOKEN") or "")
        self.lock = threading.Lock()
        self.scan_lock = threading.Lock()
        self.wake = threading.Event()
        self.state_path = self.root / "uploaded.json"
        try:
            self.uploaded = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            self.uploaded = {}

    def event(self, kind, **fields):
        """Record input before arbitration, including queued and interrupted input."""
        row = {"ts_ns": time.time_ns(), "kind": kind, **fields}
        with self.lock:
            with (self.root / "operator.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        self.wake.set()

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="mct-archive").start()

    def queue_turn(self, fl, model, sid, duration, ack):
        tag = fl["tag"]
        key = hashlib.sha256((str(self.ws) + "/" + tag).encode()).hexdigest()[:32]
        row = {"locus": self.locus, "turn": key,
               "prompt": fl["prompt"].read_text(encoding="utf-8"),
               "response": fl["resp"].read_text(encoding="utf-8") if fl["resp"].exists() else "",
               "model": model or "", "duration_s": round(duration, 1),
               "pointers": {"sid": sid, "ack": ack or "", "ws": str(self.ws),
                            "tag": tag, "interrupted": fl.get("interrupted", False),
                            "archive_source": str(fl["prompt"])}}
        _atomic(self.root / ("pending-" + key + ".json"), json.dumps(row).encode())
        self.wake.set()

    def run(self):
        while True:
            self.wake.clear()
            self.sync()
            self.wake.wait(15)

    def _post(self, route, payload):
        req = urllib.request.Request(self.base + route,
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
            req.add_header("X-Operator-Token", self.token)
        with urllib.request.urlopen(req, timeout=30) as reply:
            result = json.load(reply)
        if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
            raise RuntimeError("archive API rejected upload")
        return result

    def _files(self):
        paths = list((self.ws / "exchange").glob("turn-*"))
        paths += list(self.root.glob("*.jsonl"))
        paths += [self.ws / name for name in
                  ("session.json", "operator-guidance.md", "operator-guidance.user.md", "todo.md")]
        # Exchange streams are scoped to this workspace. Never collect unrelated
        # Codex conversations or credentials from the shared login store.
        return sorted({p for p in paths if p.is_file()})

    def sync(self):
        """Retry all changed files; checkpoints advance only after HTTP success."""
        if not self.scan_lock.acquire(blocking=False):
            return {"busy": True}
        import fcntl
        process_lock = (self.root / ".sync.lock").open("a")
        try:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            process_lock.close()
            self.scan_lock.release()
            return {"busy": True}
        sent, errors = 0, []
        try:
            try:
                self.uploaded = json.loads(self.state_path.read_text())
            except (OSError, ValueError):
                self.uploaded = {}
            if not self.locus:
                raise ValueError("MCT_LOCUS is required for database attribution")
            files = self._files()  # snapshot transcripts even while the API is offline
            for pending in sorted(self.root.glob("pending-*.json")):
                row = json.loads(pending.read_text())
                row["locus"] = self.locus
                self._post("/exchange/record", row)
                pending.unlink()
            for path in files:
                try:
                    version = path.stat().st_mtime_ns
                    raw = path.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    source = str(path)
                    state_key = self.base + "|" + self.locus + "|" + source
                    if self.uploaded.get(state_key) == digest:
                        continue
                    sid = ""
                    if path.name.startswith("transcript-"):
                        for line in raw.splitlines()[:10]:
                            try:
                                row = json.loads(line)
                                sid = row.get("sessionId") or row.get("session_id") or ""
                            except (ValueError, AttributeError):
                                continue
                            if sid:
                                break
                    self._post("/exchange/archive", {
                        "locus": self.locus, "source": source, "backend": "mct",
                        "session_id": sid, "version": version,
                        "data": base64.b64encode(raw).decode("ascii"),
                    })
                    self.uploaded[state_key] = digest
                    _atomic(self.state_path, json.dumps(self.uploaded).encode())
                    sent += 1
                except Exception as exc:
                    # Never include credentials or response bodies in status.
                    errors.append({"source": str(path), "error": type(exc).__name__})
                    break  # an outage should cost one timeout per retry cycle
        except Exception as exc:
            errors.append({"error": type(exc).__name__})
        finally:
            result = {"ts": time.time(), "locus": self.locus, "sent": sent, "errors": errors}
            _atomic(self.root / "status.json", json.dumps(result).encode())
            process_lock.close()
            self.scan_lock.release()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace")
    parser.add_argument("--locus", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    archive = Archive(args.workspace, args.locus)
    if args.once:
        result = archive.sync()
        print(json.dumps(result))
        return int(bool(result.get("errors") or result.get("busy")))
    archive.run()


if __name__ == "__main__":
    raise SystemExit(main())
