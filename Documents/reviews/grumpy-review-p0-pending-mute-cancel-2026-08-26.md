# Grumpy review — P0 pending-mute cancel

**Scope:** `yolo/p0-pending-mute-cancel` uncommitted diff  
**Date:** 2026-08-26 (America/Toronto)  
**Spec:** `Documents/specs/multi-clip-per-track-spec.md` P0 / SP3

---

## 1. First impressions

Small, focused diff. Pure-function cancel lives in `loop_model.py` where it belongs; bench wiring is minimal. Tests cover plan + footswitch + fake engine timing. That's the right shape for P0.

The launch-cancel half ships without proving the engine honours a bench-only pending clear. That makes me twitchy.

---

## 2. Architecture

**Good:** Cancel checks `pending` against **engine** `sl_state` before `effective_state` — fixes the launch-on-re-tap bug without touching LED or OSC layers.

**Good:** `cancel_pending` flag avoids overloading `expect=None` semantics.

**🟡:** Pending **launch** cancel sends **no** OSC undo. Bench clears `_pending` but `pause_off` + `trigger` already went out. If SL queues trigger at the boundary, re-tap may still launch. Mute cancel sends `mute_off` — symmetric with spec SP3. Launch cancel is spec-adjacent but unverified on hardware.

---

## 3. Code smells

### 🔴 None for mute-cancel path

Mute cancel path is coherent: plan → `mute_off` → clear pending → fake engine clears `_at_boundary`.

### 🟡 Launch cancel — engine truth drift

```python
if pending == STATE_PLAYING and sl_state in (SL_STATE_MUTE, SL_STATE_PAUSED):
    return Plan(cancel_pending=True, note="cancel pending launch")
```

No command to SL. `test_re_tap_cancels_pending_launch_without_second_trigger` only asserts bench state, not engine state after `boundary()`. **Fix direction:** Pi SP3-style test or document as bench-only until trigger-cancel verb is spiked; add engine test that `boundary()` still mutes if trigger was queued.

### 🟡 DECISIONS row contradicts rev 2 spec

Same branch adds Gate A row saying "16 tracks" and "Scene Launch 1–8". Spec rev 2 corrected to **15 tracks** and **Scene 1–7**. Doc drift on merge.

### 🟢 `_pending_since` not cleared on cancel

```python
if plan.cancel_pending:
    self._pending = None
```

Timeout expiry uses `_pending_since`; stale timestamp is harmless but sloppy. One-liner: reset `_pending_since` when clearing.

### 🟢 SCRATCH default fix duplicates constant

`sl_hud_monitor.py` hardcodes default `"14"` with comment — fine; could import from `looper_songs.SCRATCH` but import chain broke earlier. Acceptable.

---

## 4. Logic & edge cases

| Case | Handled? |
|------|----------|
| Re-tap pending mute while PLAYING | ✅ `mute_off` |
| Re-tap pending mute while OVERDUBBING | ✅ ACTIVE_PLAY |
| Re-tap pending mute on pad **down** | ✅ no-op (tested) |
| Re-tap pending launch while MUTE | ⚠️ bench only |
| Cancel during `_waiting_for_quantize()` | Unchanged — gestures blocked during quantize wait (pre-existing) |
| Cancel during tail capture | `_gesture` returns early on tail — cancel not reachable (pre-existing) |

---

## 5. Tests

**Good:** Unit + integration-style footswitch + `FakeSlEngine` boundary test for mute cancel (`test_second_tap_during_a_quantized_stop_keeps_it_playing`).

**Gap:** No test that launch cancel survives `engine.boundary()` without transitioning to PLAYING.

---

## 6. Summary

| | |
|---|---|
| **Good** | Correct fix location; mute cancel tested end-to-end through fake engine; minimal bench diff |
| **Bad** | Launch cancel may lie to the player if SL still fires queued trigger |
| **Smells** | DECISIONS Gate A row stale vs rev 2 spec; `_pending_since` not reset |

### Severity roll-up

| ID | Sev | Item |
|----|-----|------|
| G1 | 🟡 P1 | Launch cancel — no engine cancel; add boundary test or defer launch cancel from P0 |
| G2 | 🟡 P1 | DECISIONS Gate A row — align with rev 2 (15 tracks, scene 1–7) |
| G3 | 🟢 P2 | Clear `_pending_since` on cancel_pending |
