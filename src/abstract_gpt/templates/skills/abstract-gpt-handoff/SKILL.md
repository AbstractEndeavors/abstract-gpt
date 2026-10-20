---
name: abstract-gpt-handoff
description: Save the current Codex session state to the toolserver so another session can resume it. Use when asked to hand off, checkpoint, or prepare a Codex session for rollover.
---

# Toolserver handoff (Codex)

Record durable task state in the toolserver; do not treat a transcript dump as
a handoff.

1. Call `session_identity` to obtain this Codex session's id. Do not substitute
   a Claude session id or invent one.
2. Compose a concise state record: goal, operator rulings and reasons,
   decisions, verified/unverified world state, in-flight step, open questions,
   and useful pointers. Omit scratch reasoning and dead ends.
3. Call `seat_session_handoff(session_id=<id>, doc=<record>)`. If the user also
   requests a locus/task ledger, resolve it from the request,
   `HUGPY_LOCUS`, or `EXCHANGE_LOCUS`; ask if missing or ambiguous, then use
   `ledger_template` and `ledger_put` as an additional record.
4. Confirm the toolserver accepted the session handoff. Do not claim success if
   the write failed.

After success, return the toolserver's handoff pointer if provided; otherwise
give a one-line confirmation.
