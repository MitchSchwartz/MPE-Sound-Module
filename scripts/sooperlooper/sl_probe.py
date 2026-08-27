"""Is SooperLooper's COMMAND path alive? Shared by sl-health and sl-watchdog.

This exists because the read path and the write path fail independently. `/get`
reads engine state directly; `/set`, `/hit` and `save_loop` all go through
`push_nonrt_event()`, which is drained from the JACK process callback. When that
stops draining — most commonly because the engine lost its JACK client (spec §M)
— every read-only check reports a healthy engine and every command vanishes.

It also exists because the naive version of this check is dangerous. Two probers
(`sl-health` run by hand, `sl-watchdog` every 10 s) both wrote the same control,
alternating between the same two values. Health asked for 0.5, the watchdog
wrote 0.75 in the gap, health read 0.75 and declared the engine WEDGED — whose
documented remedy is `sl-restart`, which **destroys every recorded loop**. A
monitoring race must never recommend a data-losing action.

So the verdict is built to be right under contention:

  * each probe writes a value nobody else is likely to write, derived from the
    caller's own identity, so two probers do not collide by construction;
  * a value that changed to something we did **not** ask for is proof the engine
    is executing `set` commands — someone else's. That is ALIVE, not wedged;
  * only a value that did not move at all, twice, is a wedge.
"""

from __future__ import annotations

import os
import time

ALIVE = "alive"
WEDGED = "wedged"
UNREACHABLE = "unreachable"

# The control we scribble on. Restored immediately afterwards.
PROBE_CONTROL = os.environ.get("MPE_SL_PROBE_CONTROL", "dry")
PROBE_LOOP = int(os.environ.get("MPE_SL_PROBE_LOOP", "0"))

# Restore to the value the control is SUPPOSED to hold, not to whatever it held
# when we looked.
#
# "Put back what was there" sounds obviously correct and is wrong here. With two
# probers interleaving, A reads 0.0 and writes its target; B reads A's target as
# its own "before" and restores to that; the control converges on pollution
# instead of on zero. Found live: loop 0 sat at dry=0.41 while every other loop
# read 0.0 — Surge passing through the looper at 41% and doubling at the
# speakers, which is audible and permanent.
#
# `dry` has a known-correct value: wire-jack-graph.sh sets dry=0 on every loop,
# because the looper must not pass its input through. Restore to that.
PROBE_RESTORE = float(os.environ.get("MPE_SL_PROBE_RESTORE", "0.0"))


def probe_target(seed: str, before: float | None) -> float:
    """A value distinct from the current one and from other probers' choices.

    Fixed alternation between two constants is what made two probers collide.
    Deriving from the caller's name spreads them across the range instead.
    """
    offset = (sum(ord(c) for c in seed) % 17) / 100.0  # 0.00 .. 0.16
    target = 0.30 + offset
    if before is not None and abs(before - target) < 0.005:
        target += 0.20
    return round(target, 4)


def check_command_path(get, send, *, seed: str, settle_s: float = 0.5,
                       retries: int = 1) -> tuple[str, str]:
    """Round-trip a `set`. Returns (verdict, human-readable detail).

    `get(ctrl)` returns a float or None. `send(ctrl, value)` writes it.
    """
    before = get(PROBE_CONTROL)
    if before is None:
        return UNREACHABLE, f"no reply reading {PROBE_CONTROL}"

    detail = ""
    for attempt in range(retries + 1):
        target = probe_target(f"{seed}{attempt}", before)
        send(PROBE_CONTROL, target)
        time.sleep(settle_s)
        after = get(PROBE_CONTROL)

        if after is None:
            detail = f"engine stopped answering mid-probe (attempt {attempt + 1})"
            continue
        if abs(float(after) - target) < 0.01:
            _restore(send, before)
            return ALIVE, f"{PROBE_CONTROL} {before} -> {after}"
        if abs(float(after) - float(before)) > 0.01:
            # It moved, just not where we put it. Another prober's `set` landed,
            # which is direct evidence the non-realtime queue is draining.
            _restore(send, before)
            return ALIVE, (f"{PROBE_CONTROL} moved to {after} (not our {target}) "
                           f"— another prober is writing; commands execute")
        detail = (f"{PROBE_CONTROL} did not move (asked {target}, still {after}) "
                  f"on attempt {attempt + 1}")

    _restore(send, before)  # a no-op on a truly wedged engine, correct otherwise
    return WEDGED, detail


def _restore(send, _before: float | None) -> None:
    """Always to the policy value — see PROBE_RESTORE."""
    send(PROBE_CONTROL, PROBE_RESTORE)


PHANTOM = "phantom"


def check_loops_writable(get_loop, send_loop, *, num_loops: int,
                         settle_s: float = 0.12) -> tuple[str, list[int], str]:
    """Which loop indices actually accept writes?

    `check_command_path` proves the engine's command path is draining, using
    one loop. That is not the same question as "does every loop I was given
    exist", and on 2026-08-27 the difference cost a morning.

    SooperLooper reports whatever loop count you passed to `-l`, and every
    index answers `get` with plausible defaults — but only indices below the
    engine's real ceiling accept `set`. A phantom index therefore passes every
    read-based check while silently discarding configuration, which is how one
    track ended up unquantized among fourteen quantized ones with nothing
    anywhere reporting a problem.

    So this writes to each index and reads it back. `get_loop(loop, ctrl)`
    returns a float or None; `send_loop(loop, ctrl, value)` writes.

    Returns (verdict, phantom_indices, detail).
    """
    phantoms: list[int] = []
    unreachable: list[int] = []
    for loop in range(num_loops):
        before = get_loop(loop, PROBE_CONTROL)
        if before is None:
            unreachable.append(loop)
            continue
        target = probe_target(f"writable{loop}", before)
        send_loop(loop, PROBE_CONTROL, target)
        time.sleep(settle_s)
        after = get_loop(loop, PROBE_CONTROL)
        if after is None or abs(float(after) - target) >= 0.01:
            phantoms.append(loop)
        else:
            send_loop(loop, PROBE_CONTROL, PROBE_RESTORE)

    if unreachable:
        return (UNREACHABLE, phantoms,
                f"no reply from loops {unreachable} reading {PROBE_CONTROL}")
    if phantoms:
        return (PHANTOM, phantoms,
                f"loops {phantoms} answer reads but ignore writes — "
                f"the engine has fewer usable loops than it reports. "
                f"Lower MPE_SL_LOOPS to {min(phantoms)} (see sl_limits.py).")
    return ALIVE, [], f"all {num_loops} loops accept writes"
