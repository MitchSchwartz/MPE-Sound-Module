"""What every control DOES, as rows. One table, no order.

`control_registry` says which physical thing sends which note. This says what
happens when you press it. **If a routing decision lives anywhere else, that is
the bug.**

WHY THIS FILE EXISTS

Until 2026-08-30 the answer to "what does this pad do on press, on release, and
under Shift?" was spread across `sooperlooper-apc-bench.py`'s event loop,
`track_gesture.py`, `apc_transport.py` and `ShiftHoldCombo` — and the loop
resolved it with an `if`-chain whose ORDER was load-bearing. That is not a
stylistic complaint. It is the mechanism of the mk2 banking bug:
`ARROW_NOTES_MK2` claimed 0x70-0x73, the scene branch claimed the same four
notes and `continue`d forty-five lines before `handle_arrow` was reached, so the
bank buttons were structurally unreachable — and the boot banner advertised them
on every start. One hundred and twenty-six green APC tests never saw it, because
reachability was a property of statement order and nothing read statement order.

THE FIX IS THE SHAPE, NOT THE ROWS

Routing here is a **mapping keyed by control**, not a sequence of branches:

    note -> control_registry.control_for_note()   (one control; collisions
                                                   already refused at import)
    (control, mode, layer, gesture) -> Binding    (one row; collisions refused
                                                   at import, below)

There is no "first match wins", so there is no order to get wrong. A second
claim on one note cannot hide behind an earlier branch — `control_registry`
refuses it before this module is even consulted. A second claim on one
*gesture* of one control is refused by `assert_no_binding_collisions` below,
which names both source lines. The 2026-08-30 arrow bug is not fixed here; it
is **unexpressible** here.

THE RULES

1. A row names a control by its registry id. No note number appears in this
   file — stage 1's AST guard already covers it, and was verified to: injecting
   `NOTE_STOP_ALL_MK2 = 0x77` here fails
   `test_control_registry.NoteLiteralTests` twice, naming this file and the
   line. That is the enforcement, not this sentence.

2. Rows are TOTAL over (control, gesture) for every control the surface has.
   "Nothing happens" is written `NOOP`, never left out. An unbound control and
   a forgotten one look identical on the device — that is what the eight
   track-select buttons have been for six weeks — so they must not look
   identical in the source.

3. An action names its OWNER. `ACTIONS` below is the only place that says which
   module receives a press, so `control_registry.Control.owner` (a description)
   and this table (the routing) are two independent statements about the same
   thing, and `tests/test_control_registry.py` compares them. A model built by
   copying the event loop's branch order by hand — which is what that test used
   to do — drifts silently the first time someone edits the loop.

4. A row with more than one action is a control with more than one owner. That
   is spec defect D4 and it is written down rather than hidden: `shift` latches
   the bench's modifier state AND feeds `apc_transport`'s combo, in that order,
   because that is what the loop did. Two actions is a finding, not a feature.

5. Reachability is checkable without a device. `unreachable()` returns the rows
   whose control has no established note on a variant, with the registry's own
   reason. On the attached mk2 that is exactly the four bank arrows, and the
   session banner already says so.

WHAT IS NOT HERE

Timing. A HOLD row records that a hold exists, how long it is, and which module
counts the milliseconds — it does not run the clock. The clock stays where it
is (`ShiftHoldCombo`, `SlotSurface.poll_hold`, `TrackGesture.poll_hold`),
because moving it would put a timer in the 485 Hz poll path for no gain.
`tests/test_binding_table.py` asserts the bench feeds each HOLD row's declared
env var into its declared timing owner, so the number here cannot drift away
from the number that runs.

Also not here: what a lamp shows. That is `led_compositor`'s (spec §5.3).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

import control_registry as reg
from apc_panel import row_for_scene_index

# --- the vocabulary ---------------------------------------------------------

#: A note-on. The finger goes down.
PRESS = "press"
#: A note-off, or a note-on at velocity 0. The finger comes up.
RELEASE = "release"
#: Held past a threshold, fired by a poller rather than by a MIDI event.
HOLD = "hold"
#: Pressed and released *before* the hold threshold. Distinct from RELEASE:
#: every hold has a release, but only a short one is a tap. `ShiftHoldCombo`
#: has always had both edges (`poll_short` / `poll_long`); the charter's list of
#: gestures did not, and calling both of them "release" would have lost the
#: distinction the code actually makes.
TAP = "tap"
#: A continuous controller moved. Faders only.
MOVE = "move"

GESTURES: tuple[str, ...] = (PRESS, RELEASE, HOLD, TAP, MOVE)

#: Who calls the action. Not decoration: it is the difference between a row the
#: router can execute and a row that only DESCRIBES something happening
#: elsewhere, and conflating the two is how a table becomes documentation that
#: cannot fail a build.
BY_ROUTER = "router"        # the event loop's dispatch, on the MIDI event
BY_BENCH_POLL = "bench_poll"  # a bench poller notices a threshold and calls fire()
BY_OWNER = "owner"          # the owning module runs its own clock and its own action

FIRED_BY: tuple[str, ...] = (BY_ROUTER, BY_BENCH_POLL, BY_OWNER)

#: Modifier layers. `ANY_LAYER` is a row that does not care, and it expands to
#: both when collisions are checked — so "Shift+X" cannot be added later
#: alongside an existing "X on any layer" without the clash being reported.
BASE = "base"
SHIFT = "shift"
ANY_LAYER = "any"

LAYERS: tuple[str, ...] = (BASE, SHIFT)

#: `MPE_SL_MULTIGRID`. The 8x8 matrix and the single-clip row are genuinely
#: different bindings for the same pads, and pretending otherwise is how the
#: reachability model ended up describing a configuration nobody runs. The
#: appliance runs MULTIGRID (`/etc/mpe/mpe.env`, verified 2026-08-30) against a
#: code default of SINGLE, so both are modelled and both are tested.
MULTIGRID = "multigrid"
SINGLE = "single"
ANY_MODE = "any"

MODES: tuple[str, ...] = (MULTIGRID, SINGLE)


@dataclass(frozen=True)
class Action:
    """One thing that can happen, and the module it happens in."""

    name: str
    owner: str
    what: str

    def __post_init__(self) -> None:
        if self.owner != reg.UNOWNED and self.owner not in reg.OWNERS:
            raise ValueError(
                f"action {self.name!r}: {self.owner!r} is not a module "
                f"control_registry knows about ({sorted(reg.OWNERS)})"
            )
        if not self.what.strip():
            raise ValueError(f"action {self.name!r} with no description")


def _action(name: str, owner: str, what: str) -> Action:
    return Action(name=name, owner=owner, what=what)


#: Every action a row may name. Adding a row with an unknown action is a
#: ValueError at import, so a typo cannot become a control that does nothing.
ACTIONS: dict[str, Action] = {
    a.name: a
    for a in (
        _action("noop", reg.UNOWNED,
                "nothing. The press falls through the whole surface and is not "
                "even logged — which is why it has to be written down"),
        _action("scene_launch", "slot_surface",
                "SlotSurface.scene_press(row) — launch or stop the whole row"),
        _action("scene_release_consumed", "slot_surface",
                "nothing, but the event is consumed: a scene button's up edge "
                "has never reached anything else"),
        _action("slot_press", "slot_surface",
                "SlotSurface.note_down(note) — the 8x8 matrix under multigrid"),
        _action("slot_release", "slot_surface",
                "SlotSurface.note_up(note)"),
        _action("slot_delete", "slot_surface",
                "hold-to-delete on a matrix cell, timed by SlotSurface.poll_hold"),
        _action("clip_press", "track_gesture",
                "TrackGesture.on_pad_down() — the single-clip row, multigrid off"),
        _action("clip_release", "track_gesture",
                "TrackGesture.on_pad_up()"),
        _action("clip_clear", "track_gesture",
                "hold-to-clear on a clip pad, timed by TrackGesture.poll_hold"),
        _action("ignore_reserved_row", reg.UNOWNED,
                "grid rows 1-7 with multigrid off: consumed and logged under "
                "--dump-midi, because nothing is wired to them in that mode"),
        _action("latch_shift", "sooperlooper-apc-bench",
                "the event loop's modifier latch — the one every layer above "
                "is resolved against"),
        _action("transport_note", "apc_transport",
                "ShiftHoldCombo.note_event + TransportButtonLeds.note_event"),
        _action("stop_all_loops", "apc_transport",
                "Shift+StopAll released before the hold threshold — stop every "
                "loop. Fired by ShiftHoldCombo.poll_short"),
        _action("clear_all_loops", "apc_transport",
                "Shift+StopAll held past the threshold — clear every take. "
                "Fired by ShiftHoldCombo.poll_long"),
        _action("bank_scroll", "sooperlooper-apc-bench",
                "move the viewport — bank_delta_for_arrow then set_view"),
        _action("fader_move", "loop_mix",
                "LoopMix.messages_for through the coalescing sender"),
    )
}


@dataclass(frozen=True)
class Binding:
    """One control, one gesture, one layer, one mode — and what it does.

    `actions` is a tuple because one press can legitimately reach two modules
    today (rule 4). It is ordered: `shift` latches before it feeds the combo,
    which is the order the event loop ran them in and the order the layer
    resolution of the *next* event depends on.
    """

    control: str
    gesture: str
    layer: str
    mode: str
    actions: tuple[str, ...]
    #: Who runs the actions. See `FIRED_BY`.
    fired_by: str = BY_ROUTER
    why: str = ""
    #: For HOLD rows: the env var the threshold comes from, and the module that
    #: counts. Both are checked against the bench's source by the test suite, so
    #: a row cannot claim a hold nothing implements.
    hold_env: str | None = None
    timing_owner: str | None = None
    #: Where this row is written. Reported by `unreachable()` and by the
    #: collision check, so a finding names a line rather than a control id.
    defined_at: int = 0

    def __post_init__(self) -> None:
        if self.control not in reg.CONTROLS:
            raise ValueError(
                f"binding at line {self.defined_at}: no control "
                f"{self.control!r} in control_registry"
            )
        if self.gesture not in GESTURES:
            raise ValueError(f"{self.control}: unknown gesture {self.gesture!r}")
        if self.layer not in LAYERS + (ANY_LAYER,):
            raise ValueError(f"{self.control}: unknown layer {self.layer!r}")
        if self.mode not in MODES + (ANY_MODE,):
            raise ValueError(f"{self.control}: unknown mode {self.mode!r}")
        if not self.actions:
            raise ValueError(
                f"{self.control}/{self.gesture}: a row with no action is a row "
                f"nobody wrote. Say 'noop' and mean it."
            )
        for name in self.actions:
            if name not in ACTIONS:
                raise ValueError(
                    f"{self.control}/{self.gesture}: unknown action {name!r}"
                )
        if (self.gesture == HOLD) != (self.hold_env is not None):
            raise ValueError(
                f"{self.control}/{self.gesture}: a HOLD row states its "
                "threshold's env var and nothing else does"
            )
        if (self.hold_env is None) != (self.timing_owner is None):
            raise ValueError(
                f"{self.control}/{self.gesture}: a hold with no timing owner "
                "is a hold nothing counts"
            )
        if self.timing_owner is not None and self.timing_owner not in reg.OWNERS:
            raise ValueError(
                f"{self.control}: unknown timing owner {self.timing_owner!r}"
            )
        if self.fired_by not in FIRED_BY:
            raise ValueError(f"{self.control}: unknown firer {self.fired_by!r}")
        immediate = self.gesture in (PRESS, RELEASE, MOVE)
        if immediate and self.fired_by != BY_ROUTER:
            raise ValueError(
                f"{self.control}/{self.gesture}: a MIDI event is dispatched by "
                "the router; nothing else is watching for it"
            )
        if not immediate and self.fired_by == BY_ROUTER:
            raise ValueError(
                f"{self.control}/{self.gesture}: no MIDI message says 'held for "
                "three seconds'. Say which poller notices."
            )

    @property
    def owners(self) -> tuple[str, ...]:
        """Every module this row hands the event to, in order."""
        return tuple(ACTIONS[a].owner for a in self.actions)

    def layers(self) -> tuple[str, ...]:
        return LAYERS if self.layer == ANY_LAYER else (self.layer,)

    def modes(self) -> tuple[str, ...]:
        return MODES if self.mode == ANY_MODE else (self.mode,)

    def keys(self) -> tuple[tuple[str, str, str, str], ...]:
        """Every (control, mode, layer, gesture) this row answers for.

        Expanding `ANY` here rather than at lookup time is what lets the
        collision check see that a new `SHIFT` row overlaps an existing
        `ANY_LAYER` one. A first-match-wins lookup would simply have shadowed
        it, which is the mk2 arrow bug with different nouns.
        """
        return tuple(
            (self.control, mode, layer, self.gesture)
            for mode in self.modes()
            for layer in self.layers()
        )


_ROWS: list[Binding] = []


def _row(
    control: str,
    gesture: str,
    actions: tuple[str, ...],
    *,
    layer: str = ANY_LAYER,
    mode: str = ANY_MODE,
    fired_by: str = BY_ROUTER,
    why: str = "",
    hold_env: str | None = None,
    timing_owner: str | None = None,
) -> Binding:
    """Add a row, stamping the line it was written on.

    The line number is the whole reason this is a function: a collision or an
    unreachable binding then names a source location the way the compositor's
    one-writer test does, instead of naming a control id and leaving the reader
    to grep for it.
    """
    binding = Binding(
        control=control,
        gesture=gesture,
        layer=layer,
        mode=mode,
        actions=actions,
        fired_by=fired_by,
        why=why,
        hold_env=hold_env,
        timing_owner=timing_owner,
        defined_at=sys._getframe(1).f_lineno,
    )
    _ROWS.append(binding)
    return binding


# --- the rows ---------------------------------------------------------------
#
# Every one of these is what the event loop did before this stage, branch for
# branch — checked, not asserted: `tests/test_binding_table.py` runs a
# transcription of the old chain against this table over 128 notes x both
# edges x Shift up and down x both variants x both modes.
#
# Where a branch was reachable only in one mode, the row says which mode; where
# a branch swallowed an event without acting, the row says `noop` or
# `*_consumed` rather than being omitted.

# The scene column, top to bottom. Scene 1-7 launch grid rows 7..1 and do not
# care about Shift — the old `scene_press_row` withheld only the BOTTOM button,
# and these seven rows are `ANY_LAYER` for the same reason.
for _i in range(1, reg.GRID_ROWS):
    _row(f"scene_launch_{_i}", PRESS, ("scene_launch",),
         why="launch or stop the whole row; Shift is not consulted")
    _row(f"scene_launch_{_i}", RELEASE, ("scene_release_consumed",),
         why="the up edge is swallowed — it has never reached anything")

# The bottom button wears two hats and the hat is chosen at ITS OWN press-down,
# not re-read on release. Letting go of Shift first would otherwise send the
# down to the transport combo and the up to the scene handler, and the combo
# would sit there holding a button forever. `BindingRouter` latches it.
_row("stop_all_clips", PRESS, ("scene_launch",), layer=BASE,
     why="pressed alone it is grid row 0's scene launcher, and always has been")
_row("stop_all_clips", RELEASE, ("scene_release_consumed",), layer=BASE)
_row("stop_all_clips", PRESS, ("transport_note",), layer=SHIFT,
     why="Shift+StopAll: feed the combo and light the button while held")
_row("stop_all_clips", RELEASE, ("transport_note",), layer=SHIFT)
_row("stop_all_clips", TAP, ("stop_all_loops",), layer=SHIFT,
     fired_by=BY_BENCH_POLL,
     why="released before the threshold — the panic button. ShiftHoldCombo."
         "poll_short() notices, maybe_track_transport() fires this row")
_row("stop_all_clips", HOLD, ("clear_all_loops",), layer=SHIFT,
     fired_by=BY_BENCH_POLL,
     why="held past the threshold — every take is cleared. "
         "ShiftHoldCombo.poll_long() notices",
     hold_env="MPE_APC_TRACK_RESET_HOLD_MS", timing_owner="apc_transport")

# Shift reaches two modules. The loop set its latch and did NOT `continue`, so
# the same event went on to the transport combo — the only control on the
# surface with two owners, and the reason `control_registry` records it as
# contested.
_row("shift", PRESS, ("latch_shift", "transport_note"),
     why="two owners, in this order: the latch decides the layer that the next "
         "event resolves against, and apc_transport keeps its own copy")
_row("shift", RELEASE, ("latch_shift", "transport_note"))

# The bank arrows. Up/Down page a whole screen; Left/Right nudge one track and
# are gated behind Shift inside `bank_delta_for_arrow` — the gate is arithmetic
# in that function rather than two rows here, because a bare Left/Right is not
# unbound, it resolves to a delta of zero. Unreachable on mk2: no note.
for _direction in ("up", "down", "left", "right"):
    _row(f"bank_{_direction}", PRESS, ("bank_scroll",),
         why="only the down edge scrolls")
    _row(f"bank_{_direction}", RELEASE, ("noop",))

# The 8x8, under multigrid: every pad is a matrix cell, all eight rows.
for _r in range(reg.GRID_ROWS):
    for _c in range(reg.GRID_COLS):
        _row(f"grid_r{_r}_c{_c}", PRESS, ("slot_press",), mode=MULTIGRID)
        _row(f"grid_r{_r}_c{_c}", RELEASE, ("slot_release",), mode=MULTIGRID)
        _row(f"grid_r{_r}_c{_c}", HOLD, ("slot_delete",), mode=MULTIGRID,
             fired_by=BY_OWNER,
             why="SlotSurface.poll_hold runs its own clock and its own action; "
                 "the router never sees this one",
             hold_env="MPE_APC_HOLD_MS", timing_owner="slot_surface")

# The same pads with multigrid off: row 0 is the single-clip row and rows 1-7
# are wired to nothing. Row 0's hold is TrackGesture's, not SlotSurface's —
# a different implementation of the same gesture, which is why the equivalence
# test exists.
for _c in range(reg.GRID_COLS):
    _row(f"grid_r0_c{_c}", PRESS, ("clip_press",), mode=SINGLE)
    _row(f"grid_r0_c{_c}", RELEASE, ("clip_release",), mode=SINGLE)
    _row(f"grid_r0_c{_c}", HOLD, ("clip_clear",), mode=SINGLE,
         fired_by=BY_OWNER,
         why="TrackGesture.poll_hold — a second implementation of the same "
             "gesture, which is why test_multigrid_equivalence exists",
         hold_env="MPE_APC_HOLD_MS", timing_owner="track_gesture")
for _r in range(1, reg.GRID_ROWS):
    for _c in range(reg.GRID_COLS):
        _row(f"grid_r{_r}_c{_c}", PRESS, ("ignore_reserved_row",), mode=SINGLE)
        _row(f"grid_r{_r}_c{_c}", RELEASE, ("ignore_reserved_row",), mode=SINGLE)

# The track-select row. Nothing reads these. The note falls through every
# branch and is not even logged, so a wrong note number and a button nobody
# touched look the same — which is why the absence is a row.
for _i in range(1, reg.GRID_COLS + 1):
    _row(f"track_select_{_i}", PRESS, ("noop",),
         why="unowned. control_registry.unowned() is the work queue")
    _row(f"track_select_{_i}", RELEASE, ("noop",))

# Faders are CC, not notes, and they are here for the same reason the registry
# holds them: keeping them somewhere else is how they acquired their own
# private copy of the variant sniff.
for _i in range(1, reg.GRID_COLS + 1):
    _row(f"fader_{_i}", MOVE, ("fader_move",),
         why="the one track in this column, under the current bank")
_row("fader_master", MOVE, ("fader_move",), why="every loop at once")

BINDINGS: tuple[Binding, ...] = tuple(_ROWS)


# --- the invariant that has to hold -----------------------------------------

def binding_collisions(
    rows: Iterable[Binding],
) -> dict[tuple[str, str, str, str], tuple[Binding, ...]]:
    """Rows that could both match one event, every claimant named.

    Pure and takes its input, so it can be run against rows that are not in the
    table — which is the only way to show it would have caught a shadowed
    binding. A detector that has never seen a collision is not a detector.
    """
    by_key: dict[tuple[str, str, str, str], list[Binding]] = {}
    for binding in rows:
        for key in binding.keys():
            by_key.setdefault(key, []).append(binding)
    return {k: tuple(v) for k, v in sorted(by_key.items()) if len(v) > 1}


def assert_no_binding_collisions(rows: Iterable[Binding]) -> None:
    """Refuse a table where one gesture on one control has two rows.

    Called at import below, so a colliding row cannot reach a test — it cannot
    reach anything, including the appliance. The message names both source
    lines, because "which of these two runs?" is the question the old event loop
    answered with statement order and nobody could see.
    """
    rows = tuple(rows)
    clash = binding_collisions(rows)
    if not clash:
        return
    lines = []
    for (control, mode, layer, gesture), claimants in clash.items():
        where = ", ".join(f"{__name__}.py:{b.defined_at}" for b in claimants)
        lines.append(f"{control} {gesture} on {layer}/{mode}: {where}")
    raise ValueError(
        "two bindings match one event:\n  "
        + "\n  ".join(lines)
        + "\nOne gesture on one control does one thing. Delete one, or make "
          "them differ by layer or mode — do not let a lookup pick for you, "
          "and do not let the order of `if` statements pick for you either."
    )


assert_no_binding_collisions(BINDINGS)


def missing_rows() -> dict[tuple[str, str, str], tuple[str, ...]]:
    """Controls with no row for a gesture they can produce (rule 2).

    A button produces PRESS and RELEASE; a fader produces MOVE. HOLD and TAP
    are not universal — only some controls have them — so they are not
    required. Keyed per LAYER as well as per mode: a control bound on BASE and
    silent on SHIFT is a real gap, and it is the shape of the gap the bottom
    button had for the whole life of `clear_unwired_surfaces`.
    """
    wanted = {
        reg.FADER: (MOVE,),
    }
    have: dict[tuple[str, str, str], set[str]] = {}
    for binding in BINDINGS:
        for control, mode, layer, gesture in binding.keys():
            have.setdefault((control, mode, layer), set()).add(gesture)
    gaps: dict[tuple[str, str, str], tuple[str, ...]] = {}
    for control in reg.CONTROLS.values():
        required = wanted.get(control.kind, (PRESS, RELEASE))
        for mode in MODES:
            for layer in LAYERS:
                got = have.get((control.id, mode, layer), set())
                absent = tuple(g for g in required if g not in got)
                if absent:
                    gaps[(control.id, mode, layer)] = absent
    return gaps


_GAPS = missing_rows()
if _GAPS:
    raise ValueError(
        "controls with no binding row: "
        + "; ".join(
            f"{cid}/{mode}/{layer} missing {list(g)}"
            for (cid, mode, layer), g in _GAPS.items()
        )
        + ". Write `noop` — an unbound control and a forgotten one look "
          "identical on the device and must not look identical here."
    )


@dataclass(frozen=True)
class Unreachable:
    """A row the player can never trigger, and why."""

    binding: Binding
    variant: str
    reason: str

    def __str__(self) -> str:
        return (
            f"binding_table.py:{self.binding.defined_at}: "
            f"{self.binding.control}/{self.binding.gesture} on {self.variant} "
            f"is unreachable — {self.reason}"
        )


def unreachable(variant: str) -> tuple[Unreachable, ...]:
    """Rows whose control cannot send anything on `variant`.

    This is the sentence "this button does nothing", written without hardware.
    On the attached mk2 it is exactly the four bank arrows, whose notes were
    refuted on 2026-08-29 — see `control_registry.DISPUTED`. It is deliberately
    not an error: an unreachable binding is a fact about the device, and the
    session banner already tells the player banking is unavailable. It is an
    error only when it is a SURPRISE, which is what the test pins.
    """
    out: list[Unreachable] = []
    for binding in BINDINGS:
        control = reg.CONTROLS[binding.control]
        if control.kind == reg.FADER:
            if control.cc.get(variant) is None:
                out.append(Unreachable(binding, variant, "no CC on this variant"))
            continue
        if control.notes[variant] is None:
            out.append(Unreachable(
                binding, variant,
                f"no established note ({control.evidence[variant].how})",
            ))
    return tuple(out)


# --- lookup -----------------------------------------------------------------

def scene_row(control_id: str) -> int:
    """Grid row this scene button launches.

    Derived from the control's position in the registry's SCENE ordering — the
    same ordering `scene_column_notes` hands out — so the note tuple and the row
    cannot disagree. The vertical flip itself lives in `apc_panel` and is not
    re-derived here.
    """
    order = [c.id for c in reg.controls_of_kind(reg.SCENE)]
    return row_for_scene_index(order.index(control_id))


class BindingTable:
    """The rows, indexed for one variant and one mode. Built once, at startup.

    Two dicts and no iteration at lookup time: the event loop consults this on
    every MIDI byte, and the bench idle loop runs at a measured ~485 Hz.
    """

    def __init__(self, variant: str, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; one of {MODES}")
        self.variant = variant
        self.mode = mode
        # Re-prove the note invariant for the surface we are about to run
        # against, rather than trusting that the import-time check covered it.
        reg.assert_no_collisions(reg.note_claims(variant), variant)
        self._by_note: dict[tuple[int, str, str], Binding] = {}
        self._by_cc: dict[int, Binding] = {}
        self._by_control: dict[tuple[str, str, str], Binding] = {}
        self._note_control: dict[int, str] = {}
        for binding in BINDINGS:
            if mode not in binding.modes():
                continue
            control = reg.CONTROLS[binding.control]
            if control.kind == reg.FADER:
                number = control.cc.get(variant)
                if number is not None and binding.gesture == MOVE:
                    self._by_cc[number] = binding
                continue
            note = control.notes[variant]
            if note is None:
                continue
            self._note_control[note] = control.id
            for layer in binding.layers():
                if binding.gesture in (PRESS, RELEASE):
                    self._by_note[(note, layer, binding.gesture)] = binding
                else:
                    self._by_control[(control.id, layer, binding.gesture)] = binding

    def note(self, control_id: str) -> int | None:
        return reg.CONTROLS[control_id].notes[self.variant]

    def control_for_note(self, note: int) -> str | None:
        return self._note_control.get(note)

    def resolve(self, note: int, *, layer: str, gesture: str) -> Binding | None:
        return self._by_note.get((note, layer, gesture))

    def resolve_cc(self, number: int) -> Binding | None:
        return self._by_cc.get(number)

    def resolve_timed(self, control_id: str, gesture: str, *, layer: str) -> Binding | None:
        """A HOLD or TAP row — the ones no MIDI message announces."""
        return self._by_control.get((control_id, layer, gesture))

    def rows(self) -> tuple[Binding, ...]:
        """Every row live in this (variant, mode) — including HOLD and TAP."""
        return tuple(
            b for b in BINDINGS
            if self.mode in b.modes()
            and (
                reg.CONTROLS[b.control].cc.get(self.variant) is not None
                if reg.CONTROLS[b.control].kind == reg.FADER
                else reg.CONTROLS[b.control].notes[self.variant] is not None
            )
        )


class BindingRouter:
    """The table wired to one session's handlers. The event loop's whole map.

    It owns the two latches the routing decision needs — "is Shift down" and
    "did the bottom button take Shift when it went down" — because they existed
    as loop-local variables read from three different points of a 150-line
    `if`-chain, with `continue`s between them. `control_registry` records
    `shift` as contested by four independent latches; this removes the bench's
    from that list by giving it one home next to the only thing that reads it.

    It does not own the LED, the engine, or the clock. Actions do.
    """

    def __init__(
        self,
        table: BindingTable,
        *,
        actions: Mapping[str, Callable[..., None]],
        ghost=None,
    ) -> None:
        needed = {
            name
            for binding in table.rows()
            if binding.fired_by in (BY_ROUTER, BY_BENCH_POLL)
            for name in binding.actions
        }
        missing = sorted(needed - set(actions))
        if missing:
            raise ValueError(
                f"no handler for {missing} — every action this router can fire "
                "has to be wired, or a control does nothing and nothing says so"
            )
        self._table = table
        self._actions = dict(actions)
        self._ghost = ghost
        self._shift_note = table.note("shift")
        self._stop_all_note = table.note("stop_all_clips")
        self._shift_held = False
        self._stop_all_took_shift = False
        self.last: Binding | None = None

    @property
    def shift_held(self) -> bool:
        """The one modifier latch the routing is resolved against."""
        return self._shift_held

    def set_shift(self, down: bool) -> None:
        """Move the latch. Called by the `latch_shift` action, and only there.

        Deliberately an action rather than something `note_event` does behind
        the table's back: the `shift` row lists `latch_shift` first and
        `transport_note` second, so the order two owners see one press is in
        the data. It used to be the order of two statements forty-five lines
        apart with three `continue`s between them.
        """
        self._shift_held = bool(down)

    def _layer_for(self, note: int, down: bool) -> str:
        """Which layer this event resolves on.

        The bottom button's layer is latched at ITS OWN press-down and reused on
        release. Asking the live latch again on the up edge would let a player
        who releases Shift first send the down to the transport combo and the up
        to the scene handler — and the combo would sit there holding a button
        forever.
        """
        if note == self._stop_all_note:
            if down:
                self._stop_all_took_shift = self._shift_held
            return SHIFT if self._stop_all_took_shift else BASE
        return SHIFT if self._shift_held else BASE

    def note_event(self, note: int, *, down: bool | None, now: float) -> Binding | None:
        """Route one note event. Returns the row that ran, or None."""
        if down is None:
            return None
        if self._ghost is not None:
            self._ghost.note_event(note, down, now=now)
            if self._ghost.consume(note, down, now=now):
                return None
        # The layer is chosen from the latch as it stands BEFORE this event's
        # actions run, which is what the old loop did: `routing_shift` was read
        # at the top and branch 4 moved the latch forty-five lines below it.
        layer = self._layer_for(note, down)
        binding = self._table.resolve(
            note, layer=layer, gesture=PRESS if down else RELEASE
        )
        self.last = binding
        if binding is None:
            return None
        for name in binding.actions:
            self._actions[name](note, down, binding.control)
        return binding

    def cc_event(self, number: int, value: int) -> Binding | None:
        binding = self._table.resolve_cc(number)
        if binding is None:
            return None
        for name in binding.actions:
            self._actions[name](number, value, binding.control)
        return binding

    def fire(self, control_id: str, gesture: str, *, layer: str = SHIFT) -> Binding | None:
        """Run a HOLD or TAP row a bench poller has just noticed.

        The threshold is counted elsewhere — `ShiftHoldCombo` owns the
        milliseconds. What this buys is that the *consequence* is still a row:
        "held Shift+StopAll for three seconds clears every take" is in the table
        with the rest of the surface, not only in the body of a poll function
        forty lines from anything else about that button.
        """
        binding = self._table.resolve_timed(control_id, gesture, layer=layer)
        if binding is None:
            return None
        if binding.fired_by != BY_BENCH_POLL:
            raise ValueError(
                f"{control_id}/{gesture} is fired by {binding.fired_by}, not by "
                "a bench poller — two things counting one hold is the defect "
                "this table exists to make visible"
            )
        note = self._table.note(control_id)
        for name in binding.actions:
            self._actions[name](note, True, control_id)
        return binding


def for_surface(apc_label: str, *, multigrid: bool) -> BindingTable:
    """The table this session runs against."""
    return BindingTable(apc_label, MULTIGRID if multigrid else SINGLE)
