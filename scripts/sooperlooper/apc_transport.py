"""APC mini mk2 transport buttons (Communication Protocol v1.0)."""

from __future__ import annotations

import time

# Scene Launch 8 = "Stop All Clips" (used with Shift held).
NOTE_STOP_ALL_CLIPS_MK2 = 0x77
NOTE_SHIFT_MK2 = 0x7A


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
