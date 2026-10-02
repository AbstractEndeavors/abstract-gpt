# abstract-gpt

Terminal seats can run `abstract-gpt rollover <locus>` to queue a toolserver
managed rollover. The automatic gate uses 85% of Codex's reported context
window and waits for an idle parent turn with no active subagents. It writes a
toolserver ledger before `/clear` and resumes the work in the new chat.

Codex login and session management, usable as a CLI and as the optional `/gpt/*`
category in `abstract_toolserver`. Requires Python 3.11+, Linux (for HTTP login
locking), and an installed Codex CLI.

```bash
pip install -e /srv/pyit/dev/abstract_gpt
abstract-gpt oauth-status
abstract-gpt login                    # device URL + code; approve in your browser
abstract-gpt launch --                # new interactive conversation
abstract-gpt exec -- "Explain this repository"  # ephemeral noninteractive session
abstract-gpt serve                    # shared GPT/Claude/Hugpy provider console
```

`login --browser` uses normal browser login. `login --with-api-key` reads a key
from stdin via Codex itself. API key usage has separate API billing. No key is
required for the ChatGPT subscription login flow.

## Part of the hugpy orbit

```
                    ┌──────────────────────────── hugpy (fleet) ───────────────────────────┐
                    │ central + workers: platform · engine · fleet · server · media · …     │
                    │ OpenAI-compatible /v1 — every local model, incl. B (Qwen3-Coder-Next) │
                    └───────▲───────────────────────▲──────────────────────────▲───────────┘
                            │ inference             │ inference                │ B reductions
   ┌────────────────────────┴──┐   ┌────────────────┴──────────┐   ┌───────────┴───────────────┐
   │ hugpy-station             │   │ hugpy-agent               │   │ abstract-toolserver       │
   │ desktop + headless console│──▶│ agent runtime · TUI ·     │◀─▶│ comms · ledgers · boards ·│
   │ tmux seats per locus      │   │ OpenCode/qwen seats       │   │ exchanges · MCP · b_ask   │
   └────────────┬──────────────┘   └────────────┬──────────────┘   └───────────▲───────────────┘
                │ keeper/codex seats            │ --serve                      │ tools (MCP/HTTP)
   ┌────────────▼──────────────┐   ┌────────────▼──────────────┐               │
   │ abstract-gpt (Codex seat) │   │ abstract-claude serve ────┼───────────────┘
   │ abstract-claude (Claude)  │   │  └ abstract-serve-core    │
   └───────────────────────────┘   └───────────────────────────┘
          everything ships through abstract-pypit → PyPI (+ GitHub)
```

| Package | Role | PyPI |
|---|---|---|
| **hugpy** (14 lockstep dists) | the self-hosted LLM fleet: central, workers, engine, media, server | [hugpy](https://pypi.org/project/hugpy/) |
| **hugpy-station** | Electron desktop + headless backend; tmux seats, prompt composer, loop/bug scan | deb via central install links |
| **hugpy-agent** | agent runtime on the fleet; `hugpy-agent tui` over abstract-claude serve | [hugpy-agent](https://pypi.org/project/hugpy-agent/) |
| **abstract-claude** | Claude Code launch/session/rollover + `abstract-claude serve` (roles keeper/chat/worker/local) | [abstract-claude](https://pypi.org/project/abstract-claude/) |
| **abstract-serve-core** | the HTTP routes `abstract-claude serve` actually runs (queue, relay, rollover sweeps) | [abstract-serve-core](https://pypi.org/project/abstract-serve-core/) |
| **abstract-gpt** | Codex/ChatGPT seat counterpart of abstract-claude | [abstract-gpt](https://pypi.org/project/abstract-gpt/) |
| **abstract-toolserver** | one tool service per host: comms, ledgers, boards, exchanges, MCP bridge, B on call | [abstract-toolserver](https://pypi.org/project/abstract-toolserver/) |
| **abstract-pypit** | one-command publisher: bump → build → PyPI → GitHub push | [abstract-pypit](https://pypi.org/project/abstract-pypit/) |

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

`abstract-gpt serve` uses the shared Serve console when `abstract-claude` is
installed, so its model picker can host every installed GPT, Claude, and Hugpy
provider. The standalone GPT-only service remains a compatibility fallback for
older minimal installations.

## Toolserver tools inside Codex

Every `abstract-gpt launch`, `abstract-gpt exec`, MCT turn, and shared-console
Codex app-server starts with the `toolserver` MCP bridge configured through
Codex command-line options. No `codex mcp add` or `~/.codex/config.toml` entry is
needed. The bridge honors `TOOLSERVER_URL` and existing Hugpy operator
credentials. The `mcp` install extra remains as a compatibility alias.

## Automatic comms channel

`abstract-gpt launch` and `abstract-gpt exec` install a package-owned Codex
`SessionStart` hook. At startup or resume it creates (or reuses) a toolserver
channel and binds inbound messages to the Codex thread's native queue. The
shareable URL is shown at session start and added to the model's context.

```bash
abstract-gpt comms          # show the newest bound endpoint
abstract-gpt set-comms off  # opt out (use `on` to enable again)
```

The wrapper passes Codex's hook-trust bypass only for launches it controls, so
its installed hook can run without an extra confirmation step. This does not
change Codex sandbox or tool approval policy.

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

## Serve

`abstract-gpt serve --workspace /path/to/project` opens the shared Serve console
on 127.0.0.1:9124, joining it if one is already running (the standalone GPT
service used without abstract-serve-core stays on 127.0.0.1:9127). The Conversation model picker uses the authenticated backend
catalog and changes the current conversation while preserving its ID and history.
`GET /api/console/models` lists choices; `POST /api/console/switch` accepts
`session_id`, `backend: "gpt"`, and `model`. Active turns must finish first.
Station combines this catalog with Claude and Hugpy in one Serve conversation.

## In hugpy-station

- The Station's `codex` frontier backend launches the `keeper-codex` tmux seat
  with abstract-gpt's launch command (`_gpt_launch_cmd`); `gpt_station.py` in
  the Station backend wraps it. The version is pinned in the Station's
  `REQUIREMENTS.txt`, and those pins are force-applied to every Station venv.
- Toolserver tools: `gpt_login_start` / `gpt_login_poll`, `gpt_oauth_status`,
  `gpt_oauth_solution`, `gpt_restore`, `gpt_save_template`, `gpt_set_model`,
  `gpt_state`.
- Rollover sessions on the gpt backend are measured by serve-core's rollover
  sweep from the codex rollout (abstract-serve-core 0.1.15).

