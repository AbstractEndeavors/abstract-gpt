"""abstract_gpt — Codex session manager (login, launch, rollover, console)."""
try:
    from importlib.metadata import version as _v
    __version__ = _v("abstract-gpt")
except Exception:
    __version__ = "0.0.0+src"

from .toolset import get_toolset  # noqa: E402

__all__ = ["get_toolset"]
