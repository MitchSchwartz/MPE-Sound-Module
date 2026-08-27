"""Ableton-style slot matrix — what a cell press means, as pure functions.

Spec: `Documents/specs/multi-clip-per-track-spec.md` (rev 3).

Columns are **tracks** (SooperLooper loop indices 0–15, contiguous since the
seam-weld scratch loop was deleted). Rows are **slots**. At most one slot per
track is audible: launching slot B on a track playing slot A always schedules
mute-A and load-and-trigger-B for the same boundary, never a layer.

Same discipline as `loop_model.py`, for the same reasons:

* **No OSC, no MIDI, no clock.** The caller executes the plan. Everything here
  is a pure function over state, so the whole gesture vocabulary is testable
  without an engine.
* **The engine is the authority on what is *sounding*.** `sl_state` is passed
  in and never stored. What this module owns is what SooperLooper has no
  concept of: which slot is loaded in a track, what is on disk, and what the
  player has asked for but not yet had confirmed.

SooperLooper has one buffer per track, so an inactive occupied slot lives on
disk only. That is why plans carry `save_first`: switching away from a slot
whose buffer has unsaved audio must flush it before the buffer is reused, or
the take is gone. The spec calls a lost flush "a save that looks exactly the
same whether it captured the audio or not" — this module makes the flush an
explicit field rather than an assumption about call order.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from sl_loop_states import ACTIVE_PLAY, SL_STATE_MUTE, SL_STATE_PAUSED

NUM_TRACKS = 16
NUM_SLOTS = 8

# What the player has asked for and the boundary has not yet delivered.
PENDING_STOP = "stop"
PENDING_LAUNCH = "launch"
PENDING_SWITCH = "switch"

# What a press means. The caller turns these into OSC.
ACT_RECORD = "record"
ACT_LAUNCH = "launch"
ACT_STOP = "stop"
ACT_SWITCH = "switch"
ACT_CANCEL = "cancel"
ACT_CLEAR = "clear"
ACT_NOOP = "noop"


@dataclass(frozen=True)
class Slot:
    """One recorded clip. ``file`` is the only thing that survives a restart."""

    file: str
    len_s: float = 0.0
    sl_state: int = 0
    #: True when the buffer holds audio not yet written to ``file``. A slot the
    #: bench just recorded is dirty until flushed; one loaded from disk is not.
    dirty: bool = False


@dataclass(frozen=True)
class Pending:
    kind: str
    to_slot: int | None = None
    from_slot: int | None = None


@dataclass(frozen=True)
class Track:
    """One column. ``slots`` is always length NUM_SLOTS; None is empty."""

    slots: tuple[Slot | None, ...] = field(
        default_factory=lambda: (None,) * NUM_SLOTS
    )
    active_slot: int | None = None
    pending: Pending | None = None

    def slot(self, index: int) -> Slot | None:
        if not 0 <= index < NUM_SLOTS:
            return None
        return self.slots[index]

    def occupied(self, index: int) -> bool:
        return self.slot(index) is not None

    def with_slot(self, index: int, value: Slot | None) -> Track:
        slots = list(self.slots)
        slots[index] = value
        return replace(self, slots=tuple(slots))


@dataclass(frozen=True)
class SlotPlan:
    """What to do about a cell press.

    ``save_first`` means: flush the currently active slot's buffer to its file
    before touching the loop, or its audio is lost when the buffer is reused.
    """

    action: str
    track: int
    slot: int
    from_slot: int | None = None
    save_first: bool = False
    clear_pending: bool = False
    note: str = ""


def _is_playing(sl_state: int) -> bool:
    return sl_state in ACTIVE_PLAY


def _needs_flush(track: Track) -> bool:
    """True when the loaded buffer holds audio that is not on disk yet."""
    if track.active_slot is None:
        return False
    active = track.slot(track.active_slot)
    return active is not None and active.dirty


def plan_cell_press(
    *,
    track_index: int,
    track: Track,
    slot: int,
    sl_state: int,
    hold: bool = False,
) -> SlotPlan:
    """The whole cell vocabulary, per the spec's tap matrix.

    ``sl_state`` is the engine's state for this track's loop — authoritative,
    and only meaningful for the **active** slot, because that is the only slot
    with a buffer.
    """
    here = SlotPlan(action=ACT_NOOP, track=track_index, slot=slot)

    if not 0 <= slot < NUM_SLOTS:
        return replace(here, note=f"slot {slot} out of range")

    occupied = track.occupied(slot)

    if hold:
        if not occupied:
            return replace(here, note="hold on an empty slot — nothing to clear")
        return replace(here, action=ACT_CLEAR, note="clear this slot")

    # A pending action is cancelled by re-tapping the slot that owns it. For a
    # stop or a switch that is the OUTGOING slot; for a launch onto a silent
    # track there is no outgoing slot, so it is the incoming one (rev 2).
    pending = track.pending
    if pending is not None:
        owner = (
            pending.to_slot
            if pending.kind == PENDING_LAUNCH
            else pending.from_slot
        )
        if owner == slot:
            return replace(
                here,
                action=ACT_CANCEL,
                from_slot=pending.from_slot,
                clear_pending=True,
                note=f"cancel pending {pending.kind}",
            )
        # A press elsewhere while something is pending replaces the pending
        # action rather than stacking on it — one pending per track.

    active = track.active_slot
    playing = _is_playing(sl_state)

    if not occupied:
        # Recording into a non-active slot reuses the track's only buffer, so
        # the active slot has to be flushed first and the track goes silent at
        # the moment of arming. That is a locked Gate A consequence, not a bug.
        return replace(
            here,
            action=ACT_RECORD,
            from_slot=active,
            save_first=_needs_flush(track),
            note=(
                "record into empty slot"
                if active is None
                else "record into empty slot — track goes silent at arm"
            ),
        )

    if slot == active:
        if playing:
            return replace(
                here,
                action=ACT_STOP,
                from_slot=slot,
                note="queue stop at the boundary",
            )
        return replace(
            here,
            action=ACT_LAUNCH,
            note="relaunch the active slot",
        )

    if active is None:
        return replace(
            here,
            action=ACT_LAUNCH,
            note="launch — nothing playing on this track",
        )

    return replace(
        here,
        action=ACT_SWITCH,
        from_slot=active,
        save_first=_needs_flush(track),
        note=f"switch slot {active} -> {slot} at the boundary",
    )


def apply_pending(track: Track, plan: SlotPlan) -> Track:
    """The bench's own bookkeeping for a plan it has just dispatched.

    Separate from ``plan_cell_press`` so a plan can be inspected, logged, or
    dropped without the matrix having already moved.
    """
    if plan.clear_pending:
        return replace(track, pending=None)
    if plan.action == ACT_STOP:
        return replace(track, pending=Pending(PENDING_STOP, from_slot=plan.slot))
    if plan.action == ACT_LAUNCH:
        return replace(track, pending=Pending(PENDING_LAUNCH, to_slot=plan.slot))
    if plan.action == ACT_SWITCH:
        return replace(
            track,
            pending=Pending(
                PENDING_SWITCH, from_slot=plan.from_slot, to_slot=plan.slot
            ),
        )
    return track


def resolve_at_boundary(track: Track) -> Track:
    """The quantize boundary arrived: the pending action is now the truth."""
    pending = track.pending
    if pending is None:
        return track
    if pending.kind == PENDING_STOP:
        return replace(track, pending=None)
    return replace(track, active_slot=pending.to_slot, pending=None)


# --- scene rows ------------------------------------------------------------
def row_is_fully_playing(
    tracks: dict[int, Track],
    row: int,
    *,
    sl_states: dict[int, int],
) -> bool:
    """Scene LED: dark when every occupied cell in the row is playing.

    Empty columns do not count — a row of two clips, both playing, is a fully
    playing row even with fourteen empty tracks. Counting empties would leave
    every scene button permanently lit.
    """
    seen = False
    for index, track in tracks.items():
        if not track.occupied(row):
            continue
        seen = True
        if track.active_slot != row or not _is_playing(sl_states.get(index, 0)):
            return False
    return seen


def plan_scene_press(
    tracks: dict[int, Track],
    row: int,
    *,
    sl_states: dict[int, int],
) -> list[SlotPlan]:
    """Toggle a whole slot row across every track, banked or not.

    Lit row -> launch every occupied cell that is not already playing.
    Dark row -> stop every occupied cell that is playing.

    Iterates the tracks it is given, which must be all of them — restricting
    this to the visible eight would silently make the gesture mean something
    different depending on the viewport.
    """
    stop = row_is_fully_playing(tracks, row, sl_states=sl_states)
    plans: list[SlotPlan] = []
    for index in sorted(tracks):
        track = tracks[index]
        if not track.occupied(row):
            continue
        plan = plan_cell_press(
            track_index=index,
            track=track,
            slot=row,
            sl_state=sl_states.get(index, 0),
        )
        if stop:
            if plan.action == ACT_STOP:
                plans.append(plan)
        elif plan.action in (ACT_LAUNCH, ACT_SWITCH):
            plans.append(plan)
    return plans


def occupied_cells(tracks: dict[int, Track]) -> list[tuple[int, int]]:
    """Every (track, slot) holding audio — for save, and for the memory table."""
    return [
        (index, slot)
        for index in sorted(tracks)
        for slot in range(NUM_SLOTS)
        if tracks[index].occupied(slot)
    ]
