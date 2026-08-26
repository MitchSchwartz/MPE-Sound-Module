"""APC mini transport buttons (Shift, Stop All Clips, bank arrows).

Mk2: Communication Protocol v1.0 — Stop All 0x77, Shift 0x7A.
Mk1: original APC mini — Stop All 0x59 (scene launch 8), Shift 0x62.
"""

from __future__ import annotations

import time
from typing import Protocol

from led_table import (
    SCENE_LED_OFF,
    SCENE_LED_ON,
    TRACK_LED_OFF,
    TRACK_LED_ON,
    accelerating_hold_blink_on,
)

# APC mini mk2 (Communication Protocol v1.0)
NOTE_STOP_ALL_CLIPS_MK2 = 0x77
NOTE_SHIFT_MK2 = 0x7A
NOTE_TRACK8_MK2 = 0x6B

# APC mini mk1 (original — port name is usually "APC MINI" without "mk2")
NOTE_STOP_ALL_CLIPS_MK1 = 0x59
NOTE_SHIFT_MK1 = 0x62
NOTE_TRACK8_MK1 = 0x37

# Bank arrows — up, down, left, right.
#
# ⚠️ UNVERIFIED against hardware, exactly like the fader CCs in apc_faders.py.
# They are resolved per variant through the same port-name path as Shift and
# Stop All (which *do* differ between mk1 and mk2), so if the recalled numbers
# are wrong, one tuple changes and no call site does. On mk1 the arrows may be
# shift-functions of the top button row rather than notes of their own — one
# more reason not to hardcode them at a call site.
#
# Confirm with: sooperlooper-apc-bench.py --dump-midi, then press each arrow.
ARROW_NOTES_MK2 = (0x70, 0x71, 0x72, 0x73)  # up, down, left, right
ARROW_NOTES_MK1 = (0x40, 0x41, 0x42, 0x43)  # up, down, left, right


def resolve_arrow_notes(
    port_name: str,
    *,
    variant: str | None = None,
) -> dict[int, str]:
    """Return {note: "up"|"down"|"left"|"right"} for the connected APC.

    Same explicit-variant-then-port-name precedence as
    resolve_apc_transport_notes(). One surface, one way of asking what it is.
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


def resolve_shift_indicator_note(apc_label: str) -> int:
    """Track Select 8 — red LED used as Shift held indicator (Shift has no LED)."""
    if apc_label == "mk2":
        return NOTE_TRACK8_MK2
    return NOTE_TRACK8_MK1


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


class _MidiOut(Protocol):
    def send_message(self, message: list[int]) -> None: ...


class TransportButtonLeds:
    """Shift / Stop All Clips button LEDs on the APC transport row.

    Shift has no LED on mk1 or mk2 — ``shift_indicator_note`` (Track Select 8)
    shows solid red while Shift is held.

    Stop All is Scene Launch 8 (green-only hardware): solid green while held
    alone; green blink during Shift+Stop reset combo. Track 8 blinks red in
    that combo.
    """

    def __init__(
        self,
        *,
        midi_out: _MidiOut,
        shift_note: int,
        stop_all_note: int,
        shift_indicator_note: int,
        hold_s: float,
        blink_start_half_s: float = 0.35,
        blink_min_half_s: float = 0.04,
    ) -> None:
        self._midi_out = midi_out
        self._shift_note = shift_note
        self._stop_all_note = stop_all_note
        self._shift_indicator_note = shift_indicator_note
        self._hold_s = max(hold_s, 0.001)
        self._blink_start_half_s = blink_start_half_s
        self._blink_min_half_s = blink_min_half_s
        self._shift_down = False
        self._stop_down = False
        self._combo_started_at: float | None = None
        self._suppress_until_release = False
        self._last_vel: dict[int, int] = {}
        self._set_led(self._shift_indicator_note, TRACK_LED_OFF)
        self._set_led(self._stop_all_note, SCENE_LED_OFF)

    def note_event(self, note: int, down: bool) -> None:
        if note == self._shift_note:
            self._shift_down = down
        elif note == self._stop_all_note:
            self._stop_down = down
        else:
            return

        self._maybe_clear_suppress()
        if self._suppress_until_release:
            self._set_led(self._shift_indicator_note, TRACK_LED_OFF)
            self._set_led(self._stop_all_note, SCENE_LED_OFF)
            return

        if self._shift_down and self._stop_down:
            if self._combo_started_at is None:
                self._combo_started_at = time.monotonic()
        else:
            self._combo_started_at = None

        self._apply(time.monotonic())

    def poll(self) -> None:
        """Drive accelerating combo blink between MIDI events."""
        if self._suppress_until_release:
            return
        if self._shift_down and self._stop_down and self._combo_started_at is not None:
            self._apply(time.monotonic())

    def on_reset_fired(self) -> None:
        """Track reset completed — dark until both buttons are released."""
        self._suppress_until_release = True
        self._combo_started_at = None
        self._set_led(self._shift_indicator_note, TRACK_LED_OFF)
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
            track_vel = TRACK_LED_ON if blink_on else TRACK_LED_OFF
            scene_vel = SCENE_LED_ON if blink_on else SCENE_LED_OFF
            self._set_led(self._shift_indicator_note, track_vel)
            self._set_led(self._stop_all_note, scene_vel)
            return

        self._set_led(
            self._shift_indicator_note,
            TRACK_LED_ON if self._shift_down else TRACK_LED_OFF,
        )
        self._set_led(
            self._stop_all_note,
            SCENE_LED_ON if self._stop_down else SCENE_LED_OFF,
        )

    def _set_led(self, note: int, velocity: int) -> None:
        velocity = max(0, min(127, velocity))
        if self._last_vel.get(note) == velocity:
            return
        self._last_vel[note] = velocity
        self._midi_out.send_message([0x90, note, velocity])
