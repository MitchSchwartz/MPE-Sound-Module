# Looper review artifacts — 2026-08-15 index

*Last updated: 2026-08-15 (America/Toronto)*

Four independent passes on the SooperLooper control layer. **Read the index first** — they are not interchangeable.

| File | Pass | Provenance | Scope |
|------|------|------------|--------|
| [`grumpy-review-looper-2026-08-15.md`](grumpy-review-looper-2026-08-15.md) | Grumpy dev | Fresh-context agent on `dev`; ran 55 tests | Deepest static read: nine-state model, watchdog/health, stale post-reset OSC, rewrite prescription |
| [`review-audit-looper-2026-08-15.md`](review-audit-looper-2026-08-15.md) | Review audit | Audits **on-disk grumpy above** + **live Pi SSH** | **§0: orphan SL (no JACK client after jackd restart)** — explains live symptoms without races |
| [`grumpy-review-looper-composer-subagent-2026-08-15.md`](grumpy-review-looper-composer-subagent-2026-08-15.md) | Grumpy dev | Cursor Composer subagent `bed22ad6…` | Incremental fix path; explicit **`eighth_per_cycle` gap**; README/spike staleness |
| [`review-audit-looper-composer-subagent-2026-08-15.md`](review-audit-looper-composer-subagent-2026-08-15.md) | Review audit | Cursor Composer subagent `979eb46f…` | Verifies manager-session draft grumpy; **DECISIONS misread**; **test contradiction** |

Prior: [`grumpy-review-looper-2026-08-14.md`](grumpy-review-looper-2026-08-14.md) (+ embedded audit correction).

---

## How to use them

1. **Live bench broken right now?** → `review-audit-looper-2026-08-15.md` §0 (orphan SL) first.
2. **Why whack-a-mole in the code?** → `grumpy-review-looper-2026-08-15.md` (state model + races).
3. **What to fix next (patch path)?** → Composer subagent grumpy backlog + composer audit P1 matrix.
4. **Rewrite vs patch?** → On-disk grumpy argues rewrite; composer subagent + audit argue narrow P1 first.

---

## Session follow-ups (manager turn, same day)

- **Tests:** `tests/test_apc_footswitch.py` — replaced contradicting grid-survival test with engine-path hold-clear tests (per composer audit).
- **Not yet done:** P1 code fixes (`_tap` send-only, `eighth_per_cycle` on establish, `_clear_loop` occupancy, watchdog logging).

---

## Merged P1 backlog (all passes)

1. Complete grid establish OSC (`eighth_per_cycle`, phase anchor)
2. Single authority for taps/LEDs (`sl_state`; demote bench `self.state`)
3. `_clear_loop` → `note_loop_content`; engine-path grid tests
4. Serialize state updates (queue + generation counter after reset)
5. Watchdog: detect orphan SL; log repair subprocess output
6. Fake SL harness (~80 lines)
