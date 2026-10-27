"""External imports resolved independently, preserving optional dependencies."""

from hugpy_resources.lazy_imports import import_registry, resolve_import

_IMPORTS = import_registry({
    'abstract_serve_core.accounting': 'token_test',
    'abstract_serve_core.accounting.usage_db': 'DEFAULT_DB',
    'abstract_serve_core.console': 'hub',
    'abstract_serve_core.integrations.mcp': 'serve_mcp',
    'abstract_serve_core.mct.mct_archive': 'Archive backfill_exchanges',
    'abstract_serve_core.processes': 'keepalive',
    'abstract_serve_core.processes.clis': 'CliInstallError ensure_codex find install_codex',
    'abstract_serve_core.processes.keepalive': 'DEFAULT_INTERVAL_S DEFAULT_LEAD_S DEFAULT_PING KEEPALIVE_DIR',
    'abstract_serve_core.processes.seat_report': 'run',
    'abstract_serve_core.serve_cli': 'serve_main=main',
    'abstract_toolserver': '_d=discovery',
    'abstract_toolserver.client': 'ToolserverClient ToolserverError',
    'argparse': 'argparse=',
    'atexit': 'atexit=',
    'base64': 'base64=',
    'fcntl': 'fcntl=',
    'glob': 'glob=',
    'importlib.metadata': '_v=version',
    'importlib.resources': 'files',
    'importlib.util': '_iu=',
    'json': 'json=',
    'os': 'os=',
    'pathlib': 'Path',
    'prompt_toolkit': 'prompt_toolkit= PromptSession',
    'prompt_toolkit.formatted_text': 'ANSI',
    'prompt_toolkit.history': 'FileHistory',
    'prompt_toolkit.input': '_seq=ansi_escape_sequences',
    'prompt_toolkit.key_binding': 'KeyBindings',
    'prompt_toolkit.keys': 'Keys',
    'prompt_toolkit.patch_stdout': 'patch_stdout',
    'queue': 'queue=',
    're': 're=',
    'readline': 'readline=',
    'shlex': 'shlex=',
    'shutil': 'shutil=',
    'socket': 'socket=',
    'subprocess': 'subprocess=',
    'sys': 'sys=',
    'tempfile': 'tempfile=',
    'textwrap': 'textwrap=',
    'threading': 'threading=',
    'time': 'time=',
    'urllib.request': '_u=',
})

__all__ = list(_IMPORTS)


def __getattr__(name):
    return resolve_import(globals(), _IMPORTS, name, submodules=True)


def __dir__():
    return sorted(set(globals()) | set(__all__))
