"""Compatibility import; the shared implementation lives in abstract-serve-core."""
import importlib as _importlib
import sys as _sys

_sys.modules[__name__] = _importlib.import_module("abstract_serve_core.paths")
