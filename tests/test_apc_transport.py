"""APC transport combo (Shift + Stop All Clips hold)."""

import unittest
from unittest.mock import patch

from scripts.sooperlooper.apc_transport import ShiftHoldCombo


class ShiftHoldComboTests(unittest.TestCase):
    def test_fires_after_hold_with_both_down(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        with patch("scripts.sooperlooper.apc_transport.time.monotonic", side_effect=[10.0, 12.9, 13.0, 14.0]):
            combo.note_event(122, True)
            combo.note_event(119, True)
            self.assertFalse(combo.poll())
            self.assertTrue(combo.poll())
            self.assertFalse(combo.poll())

    def test_releases_cancel_pending(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        combo.note_event(122, True)
        combo.note_event(119, True)
        self.assertTrue(combo.both_down)
        combo.note_event(122, False)
        self.assertFalse(combo.both_down)


if __name__ == "__main__":
    unittest.main()
