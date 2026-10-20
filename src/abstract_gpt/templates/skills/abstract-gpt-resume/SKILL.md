---
name: abstract-gpt-resume
description: Resume a Codex task from its toolserver handoff or ledger. Use when asked to resume, continue, or pick up a handed-off task.
---

# Toolserver resume (Codex)

1. Resolve the locus from the user's request, `HUGPY_LOCUS`, or
   `EXCHANGE_LOCUS`. Ask if none is available.
2. Resolve the task from the request or list active ledger tasks for that
   locus. If the intended task is ambiguous, ask rather than choosing by
   recency alone.
3. Call `session_identity` for this Codex session, then call
   `seat_session_resume(session_id=<id>)` to find the predecessor's handoff.
   If no session handoff is available, load the selected task with
   `ledger_get(locus, task)`.
4. Before doing work, state the next actions, applicable constraints/rulings,
   and anything marked unverified that needs re-checking. Do not redo verified
   work or silently discard operator decisions.
5. Continue the in-flight step only when it is unambiguous; otherwise wait for
   the user's direction.
