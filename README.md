# abstract-gpt

Codex login and session management, usable as a CLI and as the optional `/gpt/*`
category in `abstract_toolserver`. Requires Python 3.11+, Linux (for HTTP login
locking), and an installed Codex CLI.

```bash
pip install -e /srv/pyit/dev/abstract_gpt
abstract-gpt oauth-status
abstract-gpt login                    # device URL + code; approve in your browser
abstract-gpt launch --                # new interactive conversation
abstract-gpt exec -- "Explain this repository"  # ephemeral noninteractive session
```

`login --browser` uses normal browser login. `login --with-api-key` reads a key
from stdin via Codex itself. API key usage has separate API billing. No key is
required for the ChatGPT subscription login flow.

## Toolserver

Install in the toolserver's Python environment, then add alongside its Claude merge:

```python
_merge_optional_toolset("abstract_gpt.toolset:get_toolset")
```

Restart the service to register these routes. They use the toolserver's existing
authentication and execute as its OS user (`vm_mgr` on this host):

| Route | Purpose |
| --- | --- |
| `/gpt/state` | Wrapper configuration and local login status |
| `/gpt/oauth_status` | Local credential availability; not a live entitlement probe |
| `/gpt/oauth_solution` | Login instructions |
| `/gpt/login_start` | Start device login, or return an existing attempt |
| `/gpt/login_poll` | Poll state, verification URL and one-time code |
| `/gpt/save_template` | Save config.toml privately |
| `/gpt/restore` | Restore config only when missing |
| `/gpt/set_model` | Set `default_model`; null clears wrapper default |

Use POST for mutations. Device login runs in a detached subprocess with a
15-minute timeout; a file lock prevents duplicate flows across gunicorn workers.
Poll until `authenticated`, `failed`, `expired`, or `interrupted`. After service
restart, an interrupted attempt can be started again. Failed login output remains in the private `AG_ROOT/login-output.txt` for local
diagnostics, and is cleared at the next attempt. Raw login output and stored
credentials are never returned by the HTTP tools. The device code is sensitive:
use the existing authenticated toolserver connection.

## Storage and sessions

- `CODEX_HOME`: existing Codex home, default `~/.codex`. Login and launch use the
  same store so Codex manages token refresh. No credentials are copied between users.
- `AG_ROOT`: wrapper storage, default `~/.local/share/abstract_gpt`, private mode 0700.
- `AG_CODEX_BIN`: optional executable path. Otherwise `~/.local/bin/codex`, then PATH. The user-local binary takes priority
  to avoid selecting a Snap launcher inside a restricted systemd service.
- `abstract-gpt init`, `state`, `save-template`, `restore`, `set-model MODEL`,
  `login-start`, `login-poll`, and `oauth-solution` mirror their actions.
- `launch` starts a new conversation by default; explicit Codex resume flags still
  work. `exec` adds `--ephemeral`. Neither deletes existing sessions or changes
  sandbox/approval settings. Explicit model flags override the wrapper default.
- A config template can contain user-configured secrets; it stays private and is
  never returned through the API. It does not include auth.json or transcripts.

This package does not emulate Claude's durable token export, destructive reset,
or automatic quota fallback. Codex owns its cached authentication and renewal.
A service login applies to that service account; other OS users log in separately.

## Toolserver tools inside Codex

Every `abstract-gpt launch`, `abstract-gpt exec`, MCT turn, and shared-console
Codex app-server starts with the `toolserver` MCP bridge configured through
Codex command-line options. No `codex mcp add` or `~/.codex/config.toml` entry is
needed. The bridge honors `TOOLSERVER_URL` and existing Hugpy operator
credentials. The `mcp` install extra remains as a compatibility alias.

## Validation

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Tests use a fake Codex executable; they never consume model credits or change real
credentials. Authentication behavior follows the
[official Codex documentation](https://developers.openai.com/codex/auth/).

## MCT chat

```bash
abstract-gpt mct
abstract-gpt mct /path/to/workspace --model YOUR_MODEL
abstract-gpt mct /path/to/workspace --resume
abstract-gpt mct /path/to/workspace --resume --dangerous
```

MCT uses the same pointer-exchange interaction as abstract-claude: each prompt
and response lives under `exchange/`, with a multiline terminal input box,
streaming activity, and an idle timeout (`--timeout`, default 1800 seconds,
or `MCT_TURN_IDLE_TIMEOUT`). `--plain` selects line input.

While a turn runs, ordinary messages coalesce into the next turn, `+message`
queues a separate turn, `!message` interrupts, and `todo: message` or `?message`
captures a task without calling the model. `/new` resets the conversation;
`/help` lists all commands.

Default workspace: `~/.config/abstract_gpt/mct/repl` (respects `XDG_CONFIG_HOME`).
Every launch starts a fresh thread; `--resume` continues that workspace's saved
thread. Codex credentials and configuration stay intact. `--model` overrides the
wrapper's `set-model` default. Turns run with workspace-write access and no
interactive approval prompts. `--dangerous` (also accepted as
`--dangerously-bypass-approvals-and-sandbox`) gives Codex unsandboxed access and
should only be used in an externally isolated environment. Workspace
`operator-guidance.md` supplies additional instructions.

Prompt/response files, JSONL activity, and pending archive records remain on disk.
Set `MCT_LOCUS`, `TOOLSERVER_URL` (or `EXCHANGE_INGEST_URL`) and `TOOLSERVER_TOKEN`
to enable the same retryable toolserver archive as Claude MCT. This archive includes
workspace exchanges, not unrelated conversations from the Codex login store.

The adapted MCT UI and archive retain their original notice in
`src/abstract_gpt/MCT-LICENSE.txt`.
