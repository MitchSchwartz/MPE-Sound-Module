"""APC mini transport buttons (Shift, Stop All Clips, bank arrows).

Mk2: Communication Protocol v1.0 — Stop All 0x77, Shift 0x7A.
Mk1: original APC mini — Stop All 0x59 (scene launch 8), Shift 0x62.

mk1 gotcha: Track Status notes 0x30–0x37 are the same numbers as grid row 6.
They are **not** usable as a Shift-held indicator — lighting 0x37 paints grid
pads 6-7 red. Whether the Shift button itself has an LED is NOT settled here
— see `device_facts.apc.buttons.all_have_leds`, which says it does.
"""

from __future__ import annotations

import os
import time

from led_compositor import LAYER_TRANSPORT
from led_table import (
    SCENE_LED_OFF,
    SCENE_LED_ON,
    accelerating_hold_blink_on,
)

# The panel map is canonical in apc_panel. Nothing here re-derives a note.
from apc_panel import (  # noqa: E402
    NOTE_SHIFT_MK1,
    NOTE_SHIFT_MK2,
    NOTE_STOP_ALL_CLIPS_MK1,
    NOTE_STOP_ALL_CLIPS_MK2,
    SCENE_COLUMN_MK1,
    SCENE_COLUMN_MK2,
    row_for_scene_index,
    row_for_scene_note,
    scene_index_for_row,
)

# Note numbers live in control_registry — including the two this file used to
# type out for itself, and the arrow tuple that turned out to be scene buttons.
from control_registry import (  # noqa: E402
    DISPUTED,
    MK1_TRACK_STATUS_NOTES,
    arrow_notes,
    required_note,
)

NOTE_TRACK8_MK2 = required_note("track_select_8", "mk2")

# 0x37 = grid row 6 col 7 on mk1 — NOT a side-button-only note (see module doc),
# and flatly contradicted by apc_panel, which puts the whole mk1 track row at
# 0x64-0x6B. Neither claim has evidence, this name has never had a reader, and
# reasoning has produced three wrong answers about this panel already. So it
# stays a *disputed* claim rather than a constant that looks settled.
NOTE_TRACK8_MK1 = next(
    d.claimed[0] for d in DISPUTED
    if d.control_id == "track_select_8" and d.variant == "mk1"
)

# All EIGHT right-hand buttons are scene launchers, one per grid row. The last
# one carries "Stop All Clips" as a SHIFT layer only, so pressed alone it is
# row 0's launcher. See apc_panel for the panel drawing and the measurement.
SCENE_LAUNCH_NOTES_MK1 = SCENE_COLUMN_MK1
SCENE_LAUNCH_NOTES_MK2 = SCENE_COLUMN_MK2

# mk1 Track Select 1–8 are believed to share notes with grid row 6. Named in
# the registry as what they are — grid row 6 — rather than as a note range that
# reads like a row of side buttons.
MK1_TRACK_OVERLAP_NOTES = MK1_TRACK_STATUS_NOTES

def _env_float(name: str, default: float) -> float:
    """Read a float from the environment; blank or unset means the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# The mk1 Shift ghost: DISABLED, because it was never observed (SP8, 2026-08-27).
#
# The claim this filter was built on — that Shift spuriously fires Scene 1–8 /
# Track Select notes within a few ms — has now been refuted twice on the actual
# hardware. `aseqdump -p "APC MINI"`, Shift pressed alone, produced note 0x62
# and nothing else: once during the SP6 capture, and again in SP8 with Shift as
# the only button touched. Zero ghost notes across both.
#
# Meanwhile the filter's cost is real and was being paid every session. It drops
# every Scene / Track Select note-on arriving within the window of a Shift-down,
# and a human pressing Shift+Scene as a chord lands the second note well inside
# 80 ms. So the filter was eating the genuine chord — exactly the gesture the
# scene-launch row needs — to suppress an event that does not occur.
#
# The mechanism is kept, not deleted: if the ghost turns up on another mk1 unit,
# set MPE_APC_MK1_GHOST_S and it comes back with no code change. A window of 0
# disables it (see Mk1ShiftGhostFilter.consume).
MK1_GHOST_SHIFT_S = _env_float("MPE_APC_MK1_GHOST_S", 0.0)
MK1_GHOST_STOP_S = MK1_GHOST_SHIFT_S  # alias — Stop All is scene 8 on mk1

# Bank arrows — up, down, left, right.
#
# ⚠️ UNVERIFIED against hardware on mk1, and UNKNOWN on mk2. Both live in
# control_registry with their evidence; see device_facts.apc.bank_arrows.notes.
#
# The mk2 tuple used to read (0x70, 0x71, 0x72, 0x73) and was recall. Those are
# scene buttons 1-4 — MEASURED, device_facts.apc.buttons.note_sets — so the
# bench's scene branch claimed every one of them and `continue`d forty-five
# lines before handle_arrow was reached. Banking has therefore never worked on
# the attached mk2, tracks 9-15 were unreachable from the surface, and the
# startup banner advertised the feature anyway. Empty is what we actually know.
#
# Do NOT fill this in by reasoning. Confirm with:
#   sooperlooper-apc-bench.py --dump-midi, then press each arrow.
ARROW_NOTES_MK2 = arrow_notes("mk2")  # () — unknown, see above
ARROW_NOTES_MK1 = arrow_notes("mk1")  # up, down, left, right; recall


def resolve_arrow_notes(
    port_name: str,
    *,
    variant: str | None = None,
) -> dict[int, str]:
    """Return {note: "up"|"down"|"left"|"right"} for the connected APC.

    Same explicit-variant-then-port-name precedence as
    resolve_apc_transport_notes(). One surface, one way of asking what it is.

    **Empty on mk2**, because the mk2 arrow notes are not established and the
    only claim ever made about them was the scene column. An empty map makes
    handle_arrow return False for every note, which is exactly what happens
    today by accident — the difference is that it now says so.
    """
    explicit = (variant or "").strip().lower()
    if explicit in ("mk2", "mkii", "2"):
        notes = ARROW_NOTES_MK2
    elif explicit in ("mk1", "1", "original", "mini"):
        notes = ARROW_NOTES_MK1
    else:
        name = port_name.lower()
        notes = ARROW_NOTES_MK2 if ("mk2" in name or "mkii" in name) else ARROW_NOTES_MK1
    return dict(zip(notes, ("up", "down", "left", "right")))


def bank_delta_for_arrow(direction: str, *, shift_down: bool) -> int:
    """How far the viewport moves for an arrow press. 0 = do nothing.

    Up/down page by a whole screen — with the tracks on one line there is no
    vertical axis left for them to mean, so they are the fast way across the
    16. Left/right nudge by one track and are gated behind Shift, so a bare
    arrow can still be given a non-banking job later without a relearn.
    """
    from apc_grid import NUDGE_STEP, PAGE_STEP

    if direction == "down":
        return PAGE_STEP
    if direction == "up":
        return -PAGE_STEP
    if not shift_down:
        return 0
    if direction == "right":
        return NUDGE_STEP
    if direction == "left":
        return -NUDGE_STEP
    return 0


def resolve_apc_transport_notes(
    port_name: str,
    *,
    variant: str | None = None,
) -> tuple[int, int, str]:
    """Return (shift_note, stop_all_note, label) for the connected APC."""
    explicit = (variant or "").strip().lower()
    if explicit in ("mk2", "mkii", "2"):
        return NOTE_SHIFT_MK2, NOTE_STOP_ALL_CLIPS_MK2, "mk2"
    if explicit in ("mk1", "1", "original", "mini"):
        return NOTE_SHIFT_MK1, NOTE_STOP_ALL_CLIPS_MK1, "mk1"

    name = port_name.lower()
    if "mk2" in name or "mkii" in name:
        return NOTE_SHIFT_MK2, NOTE_STOP_ALL_CLIPS_MK2, "mk2"
    return NOTE_SHIFT_MK1, NOTE_STOP_ALL_CLIPS_MK1, "mk1"


def resolve_scene_launch_notes(apc_label: str) -> tuple[int, ...]:
    """Scene Launch 1–7 notes (slot rows 0–6). Stop All is not included."""
    if apc_label == "mk2":
        return SCENE_LAUNCH_NOTES_MK2
    return SCENE_LAUNCH_NOTES_MK1


def scene_row_for_note(scene_launch_notes: tuple[int, ...], note: int) -> int | None:
    """Grid row for a scene-column note, or None. Canonical map: apc_panel."""
    return row_for_scene_note(scene_launch_notes, note)


def scene_launch_index_to_row(index: int) -> int:
    """Row beside the button at `index` from the TOP. Canonical: apc_panel."""
    return row_for_scene_index(index)


def scene_row_to_launch_index(row: int) -> int:
    """Inverse. Every row has a button — eight buttons, eight rows."""
    return scene_index_for_row(row)


def mk1_shift_ghost_notes(
    *,
    stop_all_note: int,
    scene_launch_notes: tuple[int, ...],
) -> frozenset[int]:
    """Notes that spuriously fire when Shift goes down on mk1."""
    return frozenset(scene_launch_notes) | frozenset(MK1_TRACK_OVERLAP_NOTES) | {stop_all_note}


class Mk1ShiftGhostFilter:
    """Drop mk1 ghost note-ons that arrive right after Shift (solo press)."""

    def __init__(
        self,
        *,
        shift_note: int,
        stop_all_note: int,
        scene_launch_notes: tuple[int, ...],
        ghost_s: float = MK1_GHOST_SHIFT_S,
    ) -> None:
        self._shift_note = shift_note
        self._stop_all_note = stop_all_note
        self._ghost_notes = mk1_shift_ghost_notes(
            stop_all_note=stop_all_note,
            scene_launch_notes=scene_launch_notes,
        )
        self._ghost_s = ghost_s
        self._shift_down = False
        self._shift_down_at: float | None = None
        self._stop_down_before_shift = False
        self._stop_down = False

    @property
    def shift_down(self) -> bool:
        return self._shift_down

    def note_event(self, note: int, down: bool, *, now: float) -> None:
        """Track shift/stop state for ghost detection (call before consume)."""
        if note == self._shift_note:
            if down:
                self._shift_down = True
                self._shift_down_at = now
                self._stop_down_before_shift = self._stop_down
            else:
                self._shift_down = False
                self._shift_down_at = None
        elif note == self._stop_all_note and not self.consume(note, down, now=now):
            self._stop_down = down

    def consume(self, note: int, down: bool, *, now: float) -> bool:
        """True when this event should be ignored (ghost or swallowed)."""
        if not down:
            return False
        if note not in self._ghost_notes:
            return False
        if not self._shift_down or self._shift_down_at is None:
            return False
        if note == self._stop_all_note and self._stop_down_before_shift:
            return False
        if self._ghost_s <= 0.0:
            return False  # filter off — see MK1_GHOST_SHIFT_S
        return (now - self._shift_down_at) < self._ghost_s

    def shift_solo(self) -> bool:
        """Shift held without intentional Stop All (no ghost window required)."""
        return self._shift_down and not self._stop_down


class ShiftHoldCombo:
    """Tap Shift+Stop All (release before hold_s) = short; hold both >= hold_s = long."""

    def __init__(
        self,
        *,
        shift_note: int,
        target_note: int,
        hold_s: float,
        min_short_s: float = 0.05,
    ) -> None:
        self.shift_note = shift_note
        self.target_note = target_note
        self.hold_s = hold_s
        self.min_short_s = min_short_s
        self._shift_down = False
        self._target_down = False
        self._combo_started_at: float | None = None
        self._had_both_down = False
        self._long_fired = False
        self._short_pending = False
        self._short_consumed = False

    @property
    def both_down(self) -> bool:
        return self._shift_down and self._target_down

    def _clear_combo(self) -> None:
        self._combo_started_at = None
        self._had_both_down = False
        self._long_fired = False
        # _short_pending survives until poll_short() — do not clear on key release.

    def note_event(self, note: int, down: bool) -> None:
        if note == self.shift_note:
            self._shift_down = down
        elif note == self.target_note:
            self._target_down = down
        else:
            return

        if self.both_down:
            if self._combo_started_at is None:
                self._combo_started_at = time.monotonic()
                self._short_pending = False
                self._short_consumed = False
            self._had_both_down = True
            return

        if (
            not down
            and self._had_both_down
            and self._combo_started_at is not None
            and not self._long_fired
            and not self._short_consumed
        ):
            held = time.monotonic() - self._combo_started_at
            if self.min_short_s <= held < self.hold_s:
                self._short_pending = True
                self._short_consumed = True

        if not self._shift_down and not self._target_down:
            self._clear_combo()

    def poll_long(self) -> bool:
        if self._long_fired or not self.both_down or self._combo_started_at is None:
            return False
        if (time.monotonic() - self._combo_started_at) < self.hold_s:
            return False
        self._long_fired = True
        self._short_pending = False
        self._short_consumed = True
        return True

    def poll_short(self) -> bool:
        if not self._short_pending:
            return False
        self._short_pending = False
        return True

    def poll(self) -> bool:
        """Backward-compatible alias for poll_long()."""
        return self.poll_long()


class TransportButtonLeds:
    """Stop All Clips, while a finger is on it. Nothing else.

    Stop All lights while held and blinks under the Shift+StopAll clear hold,
    accelerating as the hold completes. It is green on both models because
    green is the only colour these buttons have — measured, not assumed:
    `device_facts.apc.scene.led_observed` and `.apc.buttons.single_colour`,
    2026-08-29, five probe rounds with a positive control. Red on Stop All is
    settled as impossible, on authoritative grounds; the blink is the whole
    vocabulary that is left.

    **A transient, and only a transient.** This is one button and it is shared:
    pressed alone, Stop All is grid row 0's scene launcher, and `SlotSurface`
    paints it to say whether that row holds clips. So when no finger is on the
    combo this class submits `None` — *no opinion* — and the scene indicator
    underneath comes back. It used to submit `SCENE_LED_OFF` instead, which is
    an opinion, and it won: one tap of the most-used transport button on the
    panel left row 0's indicator dark for the rest of the session while
    `SlotSurface` believed it was lit.

    What is gone, and why. `clear_unwired_surfaces()` darkened all eight scene
    notes and grid notes 8–63 — "not wired until P3", a docstring two features
    stale — on construction, on every reconnect, and on every poll while Shift
    was held alone on mk1. Under `MPE_SL_MULTIGRID=1` those 64 controls belong
    to `SlotSurface`, and this module contains no occurrence of the word
    multigrid: it darkened a surface it had never heard of. Its job — clearing
    a lamp left lit by a previous build — is now the compositor's base layer,
    which cannot clobber an owner because it is the lowest priority rather than
    the last writer. `repaint()` and `_last_vel` went with it: one diff, at the
    wire, in `led_compositor`.

    The stale mk2 Track Select 8 lamp is cleared the same way, once, by the
    base layer. Four methods here used to re-assert it OFF on a button nothing
    reads and nothing lights.
    """

    def __init__(
        self,
        *,
        compositor,
        shift_note: int,
        stop_all_note: int,
        hold_s: float,
        apc_label: str = "mk1",
        blink_start_half_s: float = 0.35,
        blink_min_half_s: float = 0.04,
    ) -> None:
        self._compositor = compositor
        self._shift_note = shift_note
        self._stop_all_note = stop_all_note
        self._apc_label = apc_label
        self._hold_s = max(hold_s, 0.001)
        self._blink_start_half_s = blink_start_half_s
        self._blink_min_half_s = blink_min_half_s
        self._shift_down = False
        self._stop_down = False
        self._shift_down_at: float | None = None
        self._stop_down_before_shift = False
        self._combo_started_at: float | None = None
        self._suppress_until_release = False

    def note_event(self, note: int, down: bool) -> None:
        now = time.monotonic()
        if note == self._shift_note:
            if down:
                self._shift_down = True
                self._shift_down_at = now
                self._stop_down_before_shift = self._stop_down
                if self._apc_label == "mk1" and not self._stop_down_before_shift:
                    self._stop_down = False
            else:
                self._shift_down = False
                self._shift_down_at = None
        elif note == self._stop_all_note:
            if down and self._mk1_ghost_stop(now):
                pass
            else:
                self._stop_down = down
        else:
            return

        self._maybe_clear_suppress()
        if self._suppress_until_release:
            self._set_led(self._stop_all_note, SCENE_LED_OFF)
            return

        if self._shift_down and self._stop_down:
            if self._combo_started_at is None:
                self._combo_started_at = now
        else:
            self._combo_started_at = None

        self._apply(now)

    def _mk1_ghost_stop(self, now: float) -> bool:
        """True when mk1 Scene 8 (Stop All note) fired spuriously with Shift."""
        if self._apc_label != "mk1" or not self._shift_down:
            return False
        if self._stop_down_before_shift:
            return False
        if self._shift_down_at is None:
            return False
        if MK1_GHOST_STOP_S <= 0.0:
            return False  # filter off — see MK1_GHOST_SHIFT_S
        return (now - self._shift_down_at) < MK1_GHOST_STOP_S

    def poll(self) -> None:
        """Drive accelerating combo blink between MIDI events."""
        if self._suppress_until_release:
            return
        if self._shift_down and self._stop_down and self._combo_started_at is not None:
            self._apply(time.monotonic())

    def on_reset_fired(self) -> None:
        """Track reset completed — dark until both buttons are released.

        Deliberately an opinion, not a release: every take has just been
        cleared, so the scene indicator under this button would come back
        saying "row 0 holds clips" for the moment before the engine reports
        otherwise. Dark is what the player is being told, and it is held until
        the fingers lift.
        """
        self._suppress_until_release = True
        self._combo_started_at = None
        self._set_led(self._stop_all_note, SCENE_LED_OFF)

    def _maybe_clear_suppress(self) -> None:
        if self._suppress_until_release and not self._shift_down and not self._stop_down:
            self._suppress_until_release = False

    def _apply(self, now: float) -> None:
        if self._shift_down and self._stop_down and self._combo_started_at is not None:
            elapsed = now - self._combo_started_at
            blink_on = accelerating_hold_blink_on(
                elapsed,
                hold_s=self._hold_s,
                blink_after_s=0.0,
                blink_start_half_s=self._blink_start_half_s,
                blink_min_half_s=self._blink_min_half_s,
            )
            self._set_led(self._stop_all_note,
                          SCENE_LED_ON if blink_on else SCENE_LED_OFF)
            return

        if self._stop_down:
            self._set_led(self._stop_all_note, SCENE_LED_ON)
            return
        # Nothing is held. Hand the button back rather than painting it dark:
        # it is grid row 0's scene launcher when it is not being used as
        # transport, and this class has no idea whether that row holds clips.
        self._release(self._stop_all_note)

    def _set_led(self, note: int, velocity: int) -> None:
        self._compositor.submit(
            LAYER_TRANSPORT, {note: max(0, min(127, velocity))}
        )

    def _release(self, note: int) -> None:
        self._compositor.submit(LAYER_TRANSPORT, {note: None})
