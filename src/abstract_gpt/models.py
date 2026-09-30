"""One cached catalog for the standalone console and Station's model picker."""
import threading
import time

_lock = threading.Lock()
_cache = {"at": 0, "models": [], "error": ""}


def _refresh():
    if not _lock.acquire(blocking=False):
        return
    try:
        from .app_server import RpcClient
        client = RpcClient(timeout=10)
        try:
            models, cursor = [], None
            while True:
                data = client.call("model/list", {"cursor": cursor} if cursor else {})
                models.extend({"backend": "gpt", "model": m.get("model") or m["id"],
                               "label": "GPT · " + (m.get("displayName") or m.get("model") or m["id"])}
                              for m in data.get("data", []) if not m.get("hidden"))
                next_cursor = data.get("nextCursor")
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            _cache.update(models=models, error="")
        finally:
            client.close()
    except Exception as exc:
        _cache["error"] = str(exc)
    finally:
        _cache["at"] = time.monotonic()
        _lock.release()


def catalog():
    # The first request waits for discovery so a fresh picker actually has models.
    if not _cache["at"]:
        _refresh()
    elif time.monotonic() - _cache["at"] > 300:
        threading.Thread(target=_refresh, daemon=True).start()
    return {"models": [{"backend": "gpt", "model": "", "label": "GPT · configured default"},
                       *_cache["models"]], "error": _cache["error"]}
