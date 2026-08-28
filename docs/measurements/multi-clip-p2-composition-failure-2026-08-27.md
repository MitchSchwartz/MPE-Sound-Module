# P2 composition refactor — Pi failure (tail recording blink)

**Date:** 2026-08-27 (America/Toronto)  
**Branch:** `dev` @ `c1dd9a3` (deployed Pi 5)  
**Reporter:** Mitch (ear test)  
**Verdict:** **P2 assignment failed** — composition refactor did not preserve single-clip LED behaviour for ring-out / tail capture.

---

## Symptom

With `MPE_SL_MULTIGRID=1`, closing a take (second tap while recording) does **not** show the
**red/green alternating blink** (`RECORD_TO_PLAY` in `led_table.py`) that single-clip mode
has had since the tail-capture work landed. Mitch: *"still not seeing the tail recording
Blinken"* — the pad should say *recording is still running while stop is queued at the bar*.

Session was otherwise alive after a stop/wait/start fix for the APC MIDI subscription race
(same morning). This failure is **LED semantics**, not MIDI dead pads.

---

## What we claimed shipped (2026-08-27)

Commit `c1dd9a3` — "Multigrid composes LoopFootswitch instead of parallel gesture logic":

- `SlotRuntime` — slot files + load/switch only; gestures delegated
- `SlotSurface` — calls `LoopFootswitch.on_pad_down/up` for record/close/stop
- `matrix_messages(..., footswitch_leds=...)` — active cell reads `footswitch.current_led()`

**Claim:** one gesture brain per track; multigrid only adds slot bookkeeping.

---

## Root cause (code)

`LoopFootswitch.sync_from_sl()` updates `_led_transition` only inside `_sync_led()`.
That call was **skipped when `multigrid=True`**:

```python
if changed:
    ...
    if not self._multigrid:
        self._sync_led()
```

Engine enters `SL_STATE_WAIT_STOP` on the OSC poll path, not on pad down. Without
`_sync_led()` on sync, `_led_transition` never becomes `RECORD_TO_PLAY`. `current_led()`
returns a steady colour; `SlotSurface.repaint()` paints that — no tail blink.

Single-clip mode still worked: it uses `poll_led()` → `_sync_led()` on every poll when
multigrid is off.

**This is an incomplete composition refactor**, not a missing feature in `loop_model` or
`plan_gesture`. OSC / ring-out overdub may still run; the **surface lied** by omitting the
blink path.

---

## Fix (dev, not Pi-validated at write time)

Always call `_sync_led()` on engine sync. `_set_led()` already no-ops in multigrid; only
`_led_transition` state is needed for `current_led()`.

Unit regression: `tests/test_multigrid_delegates.py` —
`test_multigrid_engine_sync_arms_tail_blink_sequence`.

**Pi gate:** repeat close-take on one slot; confirm red/green alternation through the bar
before solid green + overdub pass.

---

## Related morning failure (separate)

`systemctl restart mpe-looper-session` during deploy can leave APC subscribed to nothing
while the startup banner prints — pads dead, no error. Mitigation staged on `dev`:

- `scripts/restart-looper-session.sh` (stop → wait → ALSA settle → start → proc check)
- `looper-deploy.sh` uses it instead of bare restart
- Runtime subscription watchdog in `apc-bench.py` (exit if ALSA reader lost)

See `midi_subscription.py` header comment (2026-08-27).

---

## Integration plan impact

| Phase | Was | Now |
|-------|-----|-----|
| I0 dual LED | Marked done implicitly by refactor | **Reopened** — I0 must include *all* footswitch LED state forwarded to matrix, not just "no double writer" |
| I1 close take | Partial via delegation | OSC path likely OK; **LED phase `closing` not visible** until sync fix lands |
| P2 Pi sign-off | In progress | **Failed** — checklist item 1 (record → close → solid green) not met on ear |

Full plan: [`Documents/specs/multi-clip-integration-plan.md`](../../Documents/specs/multi-clip-integration-plan.md).

---

## Lessons (R&D)

1. **Composition ≠ call the same methods.** Multigrid must forward *every* output channel
   single-clip used: OSC (delegated), `_led_transition` (missed), `loop_pos` for overdub wrap
   (verify on Pi).
2. **Test the adapter, not only the delegate.** `test_multigrid_delegates` proved overdub
   on close but never asserted `WAIT_STOP` → `current_led()` animation.
3. **Copy the contract from `TransitionBlinkTests`**, not just gesture commands — those tests
   live in `test_apc_footswitch.py` and were green while multigrid regressed.

---

## Next actions

1. Merge sync fix + unit test; `mpe looper deploy dev`; Mitch re-run tail blink on column 0.
2. Audit multigrid for other `if not self._multigrid` skips (`poll_led`, `_sync_led` on
   `_gesture` is OK — confirm `_gesture` still runs `_sync_led` on pad path).
3. Add integration-plan gate: multigrid must pass `TransitionBlinkTests` equivalent via
   `current_led()` before P2 Pi checklist.
