"""APC mini mk2 transport buttons (Communication Protocol v1.0)."""

from __future__ import annotations

import time

# Scene Launch 8 = "Stop All Clips" (used with Shift held).
NOTE_STOP_ALL_CLIPS_MK2 = 0x77
NOTE_SHIFT_MK2 = 0x7A


class ShiftHoldCombo:
    """Fire once after shift and target notes are both held for hold_s seconds."""

    def __init__(
        self,
        *,
        shift_note: int,
        target_note: int,
        hold_s: float,
    ) -> None:
        self.shift_note = shift_note
        self.target_note = target_note
        self.hold_s = hold_s
        self._shift_down = False
        self._target_down = False
        self._combo_started_at: float | None = None
        self._fired = False

    @property
    def both_down(self) -> bool:
        return self._shift_down and self._target_down

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
                self._fired = False
        else:
            self._combo_started_at = None
            self._fired = False

    def poll(self) -> bool:
        if self._fired or not self.both_down or self._combo_started_at is None:
            return False
        if (time.monotonic() - self._combo_started_at) < self.hold_s:
            return False
        self._fired = True
        return True
