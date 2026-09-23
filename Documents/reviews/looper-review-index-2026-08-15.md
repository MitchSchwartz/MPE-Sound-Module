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

---

## Cycle findings (carried in 2026-09-23 before the cut)

### `grumpy-review-looper-2026-08-15.md`


1. **Serialize all state mutation onto one thread.** Replace `ThreadingOSCUDPServer` + direct `sync_from_sl` with a `queue.Queue` drained by the main loop, plus a generation counter discarding updates registered before a reset. Fixes reset-still-quantized and green-with-no-audio at the root; eliminates the `GridState` and `MidiOut` races.
2. **Make the watchdog report its repair.** Check `returncode`, log `stdout`/`stderr`, drop `need_cmd oscsend` from `wire_connect`. Three lines turning a silent permanent failure into a diagnosable one.
3. **`_clear_loop` must call `grid.note_loop_content(loop, False)`** and fire `_on_grid_dropped`; stop fabricating `sl_state`. Delete the contradicting test and replace it with the two it should have been.
4. **Render the LED from `sl_state` alone; dispatch `_tap` from `sl_state` alone.** Delete `self.state` and `self.quantized`. Collapses the two contradictory state models and closes the green-over-empty hole.
5. **Add the missing "queued to stop" state** and stop clearing `_launch_queued` on every `Mute` poll. Without these the design's core promise — solid means it happened, blink means it's coming — is false for half the transitions.

### `review-audit-looper-2026-08-15.md`


| Priority | Issue | Verdict | Effort | Depends on |
|---|---|---|---|---|
| **P0** | **Add a JACK-client-visibility check to `sl-watchdog.py`**; when SL is an orphan, say so by name ("engine is not on the JACK bus — jackd restarted under it; `mpe looper sl-restart` will fix it and WILL destroy loops") instead of endlessly retrying an impossible `jack_connect` | NEW ✅ | Quick fix | — |
| **P0** | **Fix the `if srcs and ...` guard (A8)** so an empty playback graph is reported, not swallowed; distinguish "jack_lsp failed" from "nothing connected" in `playback_sources()` | NEW ✅ | Quick fix | — |
| **P0** | **`reset_all_loops:444` → `pause_on`** (and `stop-all-loops.sh` → `pause_on`, once, not twice) | NEW ✅ | Quick fix | — |
| **P1** | **Log the watchdog's repair output** — `returncode`, `stdout`, `stderr`, and the observed `playback_sources()` on failure | ✅ (A1) | Quick fix | — |
| **P1** | **Restore `dry` unconditionally** in `sl-watchdog.py` *and* `sl-health.py`; better, probe a control with no audible effect | ✅ (A7/G3) | Quick fix | — |
| **P1** | **Render the LED from `sl_state` only** — do what the comment at `:213-215` already says | ✅ (B6) | Half-day | — |
| **P1** | **Dispatch `_tap` from `sl_state`, not `self.state`**; delete `self.state` and `self.quantized` (use `grid.established`) | ✅ (B5/B10) | Half-day | LED fix |
| **P1** | **Delete `spike-internal-sync-phase.py`** and the four dead JACK-timebase files `DECISIONS.md` already condemned | NEW ✅ | Quick fix | — |
| **P1** | **Re-alarm on every wedge cycle** so the alarm file is a heartbeat, not a tombstone | NEW ✅ | Quick fix | — |
| **P2** | **Serialize OSC updates onto the main loop** via `queue.Queue` (`ThreadingOSCUDPServer` → `BlockingOSCUDPServer` on its own thread, or a `queue` drained by the 2 ms poll). Kills D2/D3/D4/D5 by construction | ⚠️ (D2/D3) ✅ (D4) | Multi-day | LED/dispatch fixes |
| **P2** | **Add the "queued to stop" state**; clear `_launch_queued` only on the transition into `PLAYING` | ✅ (B8/B9) | Half-day | LED fix |
| **P2** | **Re-apply `apply_grid_sync` on detected engine restart** (generation counter on SL's pid or an engine-identity probe) | NEW ✅ (G6) | Half-day | — |
| **P2** | **Collapse the four state enums onto `sl_loop_states.py`**; add `10: Mute`, decide on `5: Overdubbing` | ✅ (C5) | Half-day | — |
| **P2** | **Build the `FakeEngine`** (~80 lines): consumes commands, emits a state feed, and can be told to stop draining its queue so §0 is reproducible in a test | ✅ | Half-day | — |
| **P2** | **Restore the transport-mode probe or delete the `transport` branch** — §J must stop being a lie | ✅ (C2) | Quick fix | — |
| **P3** | Delete dead code: `_osc_send`, `LED_YELLOW_BLINK`, `num_loops`, `ACTIVE_RECORD`, `set_count_in`, `anchor_phase`+`display_bpm` imports, `beat_and_bar`, `beat_and_bar_from_transport` and their tests | ✅ | Quick fix | — |
| **P3** | Extract `SlClient` shared by the four OSC processes | ✅ | Half-day | — |
| **P3** | Pass `GridState` to session-level functions instead of excavating it from a footswitch | ✅ (B12) | Quick fix | — |
| **P3** | One module identity: make production import `scripts.sooperlooper.*` | ✅ (F1) | Quick fix | — |
| **P3** | `rtmidi` callback instead of the 2 ms busy-poll | ✅ (F4) | Half-day | queue serialization |
| — | ~~Drop `need_cmd oscsend` from `wire_connect`~~ | ❌ | — | Falsified; do it as hygiene if you like, but not as a fix |
| — | ~~`_clear_loop` must call `note_loop_content(loop, False)`~~ | ❌ | — | Contradicts `DECISIONS.md` and the test that encodes it |
| — | ~~Delete `test_apc_footswitch.py:120-133` as bug-cementing~~ | ❌ | — | It encodes the spec. Fix its stray `on_pad_up()` instead |

---

