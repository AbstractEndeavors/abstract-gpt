---
name: abstract-gpt-rollover
description: Request or configure a managed Codex context rollover through the toolserver. Use when asked to roll over, checkpoint and restart, or change the rollover threshold/idle policy.
---

# Managed rollover (Codex)

Resolve the locus from the user's request, `HUGPY_LOCUS`, or `EXCHANGE_LOCUS`;
ask if none is available. Use `seat="codex"` for every `seat_rollover` call.

- With no policy settings, request a managed rollover now by calling
  `seat_rollover(locus=<locus>, seat="codex", action="request")`.
- With `threshold=<tokens>` and/or `idle=<seconds>`, configure the policy via
  `seat_rollover(locus=<locus>, seat="codex", action="configure",
  threshold=<tokens>, idle_gate_s=<seconds>)`.
- For a status question, call `seat_rollover(locus=<locus>, seat="codex",
  action="status")`.

Report the returned phase and effective policy. Do not issue `/clear` or start
a second Codex process yourself: `abstract-gpt`'s existing rollover watcher
owns the Codex-specific handoff, clear, and continuation sequence.
