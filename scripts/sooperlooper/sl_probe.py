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
  * only a value that did not move at all, twice, on every candidate control,
    is a wedge.

**The control we scribble on must not be in the audio path** (2026-08-27).
`dry` was the original choice and it is audible: the graph is deliberately
parallel — Surge reaches the speakers directly *and* through every loop input
(wire-jack-graph.sh, fail-open) — so the looper is silent on the passthrough
only because `dry` is pinned to 0 on every loop. Writing dry≈0.3-0.46 for the
0.5 s settle window mixes a second copy of the player's live signal back in,
one input-latency late. On the instrument that is a swell that lurches loud for
about a second and drops back, every watchdog interval, while nothing is even
looping. Reported live 2026-08-27; the mechanism was already written down in
this file's own restore comment and still went unnoticed for a day.

Hence PROBE_CONTROLS: an ordered chain, tried until one lands. The engine is
the authority on which controls accept a write, and this file cannot ask it at
import time, so it does not guess — it tries. `dry` stays in the chain, last,
because it is the one control measured to work; a healthy engine that accepts
the first candidate never touches it. A candidate the engine does not support
is indistinguishable from a wedge on that control alone, which is exactly why
the chain must be exhausted before any WEDGED verdict is returned.
"""

from __future__ import annotations

import os
import time

ALIVE = "alive"
WEDGED = "wedged"
UNREACHABLE = "unreachable"

# Ordered candidates, first that lands wins.
#
# `rec_thresh` is the threshold-record trigger level: a float in the same 0..1
# range as the probe targets, and nothing in this rig uses threshold recording
# (record is pad-driven — grep rec_thresh: no other hit in the repo). Writing it
# changes no audio and arms nothing, and it is restored to the SooperLooper
# default of 0 immediately.
#
# `dry` is the fallback and must stay last: it is in the audio path (see the
# module docstring), and it is also the only candidate with a live measurement
# behind it. If a future engine build stops accepting `rec_thresh`, the chain
# degrades to the audible-but-working probe rather than to a false WEDGED.
#
# MPE_SL_PROBE_CONTROL (singular, legacy) pins the chain to exactly one control.
_DEFAULT_PROBE_CONTROLS = "rec_thresh,dry"
_legacy_single = os.environ.get("MPE_SL_PROBE_CONTROL", "").strip()
PROBE_CONTROLS: tuple[str, ...] = tuple(
    c.strip()
    for c in (
        _legacy_single
        or os.environ.get("MPE_SL_PROBE_CONTROLS", _DEFAULT_PROBE_CONTROLS)
    ).split(",")
    if c.strip()
) or ("dry",)

# The control named in single-control messages and by callers that still import
# it. The head of the chain, not the only thing written.
PROBE_CONTROL = PROBE_CONTROLS[0]

PROBE_LOOP = int(os.environ.get("MPE_SL_PROBE_LOOP", "0"))

# Controls that are audible when written. Falling back onto one of these is a
# working health check and a live level fault at the same time, so it must not
# pass quietly: a probe that reports ALIVE while making the instrument swell is
# exactly the "reads the same whether broken or fine" shape that cost this
# project a day. The verdict stays ALIVE — it is true — and carries the warning.
AUDIO_PATH_CONTROLS = frozenset({"dry", "wet", "input_gain", "feedback"})

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
# Every candidate has a known-correct resting value and it is 0.0: `dry` because
# wire-jack-graph.sh pins it there (the looper must not pass its input through),
# `rec_thresh` because that is the engine default and nothing here arms
# threshold recording. A candidate whose resting value is NOT 0 must be added
# here rather than assumed.
_PROBE_RESTORE_BY_CONTROL: dict[str, float] = {
    "dry": 0.0,
    "rec_thresh": 0.0,
}
PROBE_RESTORE = float(os.environ.get("MPE_SL_PROBE_RESTORE", "0.0"))


def probe_restore_for(control: str) -> float:
    """The resting value this control must be left at."""
    if "MPE_SL_PROBE_RESTORE" in os.environ:
        return PROBE_RESTORE
    return _PROBE_RESTORE_BY_CONTROL.get(control, PROBE_RESTORE)


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


def _restore(send, control: str) -> None:
    """Always to the policy value — see probe_restore_for."""
    send(control, probe_restore_for(control))


def _probe_control(control, get, send, *, seed, settle_s, retries):
    """Round-trip a `set` on ONE control. Returns (verdict, detail).

    Restore is in a `finally`: every path that wrote must put the control back,
    including the ones that raise and the ones that give up. The version of
    this that restored at each return statement leaked on the exception path,
    and its sibling in check_loops_writable leaked on the timeout path — which
    is how a loop ends up parked at dry=0.41 forever.
    """
    before = get(control)
    if before is None:
        return UNREACHABLE, f"no reply reading {control}"

    wrote = False
    detail = ""
    try:
        for attempt in range(retries + 1):
            target = probe_target(f"{seed}{control}{attempt}", before)
            send(control, target)
            wrote = True
            time.sleep(settle_s)
            after = get(control)

            if after is None:
                detail = (f"engine stopped answering mid-probe on {control} "
                          f"(attempt {attempt + 1})")
                continue
            if abs(float(after) - target) < 0.01:
                return ALIVE, f"{control} {before} -> {after}"
            if abs(float(after) - float(before)) > 0.01:
                # It moved, just not where we put it. Another prober's `set`
                # landed, which is direct evidence the nonrt queue is draining.
                return ALIVE, (f"{control} moved to {after} (not our {target}) "
                               f"— another prober is writing; commands execute")
            detail = (f"{control} did not move (asked {target}, still {after}) "
                      f"on attempt {attempt + 1}")
    finally:
        if wrote:
            _restore(send, control)

    return WEDGED, detail


def check_command_path(get, send, *, seed: str, settle_s: float = 0.5,
                       retries: int = 1, controls=None) -> tuple[str, str]:
    """Round-trip a `set`. Returns (verdict, human-readable detail).

    `get(ctrl)` returns a float or None. `send(ctrl, value)` writes it.

    Walks PROBE_CONTROLS until one round-trips. A control the engine does not
    support looks exactly like a wedge *on that control*, so no single control
    may decide the verdict: only after every candidate has failed — including
    `dry`, the one with a live measurement behind it — is this a WEDGED engine.
    Getting that backwards would recommend `sl-restart`, which destroys takes,
    because of a typo in a control name.
    """
    candidates = tuple(controls) if controls else PROBE_CONTROLS
    details: list[str] = []
    saw_wedge = False

    for control in candidates:
        verdict, detail = _probe_control(
            control, get, send, seed=seed, settle_s=settle_s, retries=retries
        )
        if verdict == ALIVE:
            if control in AUDIO_PATH_CONTROLS and len(candidates) > 1:
                refused = ", ".join(c for c in candidates if c != control)
                detail += (f"  [WARNING: probing {control} is AUDIBLE — it mixes "
                           f"the live input back in for {settle_s:.1f}s every "
                           f"cycle, heard as volume swells. {refused} refused "
                           f"the write; set MPE_SL_PROBE_CONTROLS to a control "
                           f"this engine build accepts]")
            return ALIVE, detail
        if verdict == WEDGED:
            saw_wedge = True
        details.append(detail)

    joined = "; ".join(d for d in details if d)
    if not saw_wedge:
        return UNREACHABLE, joined or "no reply from any probe control"
    return WEDGED, joined


PHANTOM = "phantom"


def check_loops_writable(get_loop, send_loop, *, num_loops: int,
                         settle_s: float = 0.12,
                         controls=None) -> tuple[str, list[int], str]:
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

    A loop is only a phantom if it refuses **every** candidate control, for the
    same reason check_command_path exhausts the chain: an unsupported control
    would otherwise report all fifteen loops phantom and tell the player to set
    MPE_SL_LOOPS=0.

    Returns (verdict, phantom_indices, detail).
    """
    candidates = tuple(controls) if controls else PROBE_CONTROLS
    phantoms: list[int] = []
    unreachable: list[int] = []
    used: str | None = None

    for loop in range(num_loops):
        # Once a control has proven itself on one loop, stay on it: the others
        # are only reached while we still do not know which the engine takes.
        order = ([used] + [c for c in candidates if c != used]) if used else list(candidates)
        accepted = False
        # "Read it but it ignored the write" outranks "could not read it" when
        # the candidates disagree: one readable control is enough to prove the
        # loop answers, so the remaining question is whether it takes a write.
        saw_phantom = False
        for control in order:
            verdict = _loop_accepts(
                get_loop, send_loop, loop=loop, control=control, settle_s=settle_s
            )
            if verdict == ALIVE:
                used = control
                accepted = True
                break
            if verdict == PHANTOM:
                # Reads fine, ignored the write. Could be a phantom loop or an
                # unsupported control — the next candidate tells them apart.
                saw_phantom = True
        if accepted:
            continue
        if saw_phantom:
            phantoms.append(loop)
        else:
            unreachable.append(loop)

    if unreachable:
        return (UNREACHABLE, phantoms,
                f"no reply from loops {unreachable} reading "
                f"{'/'.join(candidates)}")
    if phantoms:
        return (PHANTOM, phantoms,
                f"loops {phantoms} answer reads but ignore writes — "
                f"the engine has fewer usable loops than it reports. "
                f"Lower MPE_SL_LOOPS to {min(phantoms)} (see sl_limits.py).")
    return ALIVE, [], f"all {num_loops} loops accept writes"


def _loop_accepts(get_loop, send_loop, *, loop: int, control: str,
                  settle_s: float) -> str:
    """ALIVE / PHANTOM / UNREACHABLE for one (loop, control) pair.

    Restore is unconditional once we have written, in a `finally`. The bug this
    replaces restored only on the success branch, so a loop whose read-back
    timed out — a live possibility under load with a 0.12 s settle — was
    classified a phantom and *left holding the probe value*. On `dry` that is a
    permanent second copy of the player's signal at the speakers.
    """
    before = get_loop(loop, control)
    if before is None:
        return UNREACHABLE

    wrote = False
    try:
        target = probe_target(f"writable{control}{loop}", before)
        send_loop(loop, control, target)
        wrote = True
        time.sleep(settle_s)
        after = get_loop(loop, control)
        if after is None or abs(float(after) - target) >= 0.01:
            return PHANTOM
        return ALIVE
    finally:
        if wrote:
            send_loop(loop, control, probe_restore_for(control))
