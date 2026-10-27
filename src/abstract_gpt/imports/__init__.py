"""Public access to shared constants and external imports.

External imports are loaded on first access so importing constants does not
force dependencies used only by other features to load.
"""

from .constants import (
    CYAN,
    DIM,
    BOLD,
    RESET,
    WHITE,
    PROMPT,
    SPIN,
    _FENCE_RE,
    _LIST_RE,
    SYSTEM,
    ELICITATION,
    OUTPUT_CAP,
    HOOK_MARKER,
    MODES,
)

from . import init_imports as _boundary

_EXTERNAL_NAMES = frozenset(_boundary.__all__)
__all__ = [
    'CYAN',
    'DIM',
    'BOLD',
    'RESET',
    'WHITE',
    'PROMPT',
    'SPIN',
    '_FENCE_RE',
    '_LIST_RE',
    'SYSTEM',
    'ELICITATION',
    'OUTPUT_CAP',
    'HOOK_MARKER',
    'MODES',
] + _boundary.__all__


def __getattr__(name):
    if name not in _EXTERNAL_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_boundary, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
