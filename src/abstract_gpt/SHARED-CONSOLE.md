# Shared Claude / GPT console

`abstract-gpt serve` and `abstract-claude serve` start the same web service.
Run one per locus/state directory. Install `abstract-gpt[serve]` (GPT 0.1.3,
Claude 0.1.43+) or add `abstract-claude[gpt]` to the existing console.

The **Sessions · Claude / GPT** button opens the shared session view. Choose
backend, workspace, optional model, and—for GPT—reasoning effort and execution
permissions. Backend choice is fixed for the conversation. Changing backends
requires a new session and explicit handoff; native session IDs are not portable.

    abstract-gpt serve --host 127.0.0.1 --port 9124

The launcher uses the existing console's `AC_ROOT`, `AC_HOST`, `AC_PORT`,
`AC_UI_BASE`, and `AC_UI_DIST`. If using a custom UI copy through `AC_UI_DIST`,
include the packaged `backend-sessions.js` asset and its index script tag.

## Intake and recovery

Every chat submission, including `/api/session/chat`, enters the shared durable
queue. B compiles the originals before dispatch. Arrivals during compilation
invalidate the draft and join that batch. Arrivals during a backend turn wait
for the next boundary. The server drains the queue without requiring a browser.

Configure `AC_LOCAL_LLM_URL` (full chat-completions endpoint),
`AC_LOCAL_LLM_MODEL`, and optionally `AC_LOCAL_LLM_KEY`. The existing
`AC_TIDY_WORKER` fallback remains available. Deterministic joining does not count
as successful B mediation. If both paths fail, originals remain queued and the
session is held. Use **Resume queued messages** after recovery and review.
Backend failures and interrupted turns are also held: they may already have
made changes, so they are never automatically retried.

State: `<AC_ROOT>/console.sqlite3`, overridden by `AC_CONSOLE_DB`. It holds
originals, event history, and console-to-native session mappings. A process
lease prevents duplicate ownership. After restart, uncertain running turns
are held for explicit review. Use SQLite backup rather than copying a live DB.

## GPT adapter

Uses `codex app-server` over stdio. Credentials stay in native `CODEX_HOME`;
none are copied to console state or exposed by these endpoints. `AG_CODEX_BIN`
selects the executable. The `AG_ROOT` default model is honored, otherwise the
native Codex configuration selects the model.

Default permissions: `workspace-write` / `on-request`. Explicit unrestricted
sessions use `danger-full-access` / `never`. Command, file, and permission
approvals are answered using their original request IDs. Interrupt uses
`turn/interrupt`. Other server requests receive an explicit unsupported error;
interactive tool question forms are not implemented yet.

Protocol checked against Codex CLI **0.154.0** schemas and live handshake.
App-server is marked experimental; validate adapter contracts when upgrading.

## HTTP contract

All routes are under `/api/console/` and the configured UI base path:

| Route | Method | Purpose |
| --- | --- | --- |
| `sessions` | GET / POST | List/create (`backend`, `cwd`, `model`, `effort`, `dangerous`) |
| `chat` | POST | Persist a prompt and return receipt IDs/event cursor |
| `events?id=...&since=...` | GET | Ordered replay plus queue/busy status |
| `queue` | GET / POST | Read/edit/remove/hold/retry originals |
| `interrupt` | POST | Interrupt the selected session |
| `approval` | POST | Reply to the original approval request ID |

Queue actions: `update`, `remove`, `clear`, `auto`, `retry`. Mutations after
creation require `session_id`. Approval decisions: `accept`, `acceptForSession`,
`decline`, `cancel`. Access control remains with the existing service/proxy;
retain the configured local bind.

Legacy Claude chat, queue, and history routes adopt shared sessions. Native
Claude usage collection, roster, and rollover code remain in place. GPT usage
events preserve Codex fields; Claude cache TTLs and costs are not assumed.

## Testing

Run both test directories with both `src` directories on `PYTHONPATH`.
Tests cover transport, approval IDs, interrupt, native resume, simultaneous
sends, compilation-time arrivals, queue draining without a browser, failure
retention, restart recovery, legacy HTTP routes, and the Claude subprocess
bridge. They require no frontier inference.
