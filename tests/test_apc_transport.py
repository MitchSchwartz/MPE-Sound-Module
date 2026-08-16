"""APC transport combo (Shift + Stop All Clips hold)."""

import unittest
from unittest.mock import patch

from scripts.sooperlooper.apc_transport import (
    NOTE_SHIFT_MK1,
    NOTE_SHIFT_MK2,
    NOTE_STOP_ALL_CLIPS_MK1,
    NOTE_STOP_ALL_CLIPS_MK2,
    ShiftHoldCombo,
    resolve_apc_transport_notes,
)


class ResolveApcTransportNotesTests(unittest.TestCase):
    def test_mk1_from_port_name(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC MINI:APC MINI MIDI 1 32:0")
        self.assertEqual(label, "mk1")
        self.assertEqual(shift, NOTE_SHIFT_MK1)
        self.assertEqual(stop, NOTE_STOP_ALL_CLIPS_MK1)

    def test_mk2_from_port_name(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC mini mk2")
        self.assertEqual(label, "mk2")
        self.assertEqual(shift, NOTE_SHIFT_MK2)
        self.assertEqual(stop, NOTE_STOP_ALL_CLIPS_MK2)

    def test_explicit_variant_override(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC MINI", variant="mk2")
        self.assertEqual(label, "mk2")
        self.assertEqual(shift, NOTE_SHIFT_MK2)


class ShiftHoldComboTests(unittest.TestCase):
    def test_fires_long_after_hold_with_both_down(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[10.0, 12.9, 13.0, 14.0],
        ):
            combo.note_event(122, True)
            combo.note_event(119, True)
            self.assertFalse(combo.poll_long())
            self.assertTrue(combo.poll_long())
            self.assertFalse(combo.poll_long())

    def test_short_on_release_before_hold(self) -> None:
        combo = ShiftHoldCombo(
            shift_note=NOTE_SHIFT_MK1,
            target_note=NOTE_STOP_ALL_CLIPS_MK1,
            hold_s=3.0,
        )
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[10.0, 10.2, 10.3],
        ):
            combo.note_event(NOTE_SHIFT_MK1, True)
            combo.note_event(NOTE_STOP_ALL_CLIPS_MK1, True)
            combo.note_event(NOTE_STOP_ALL_CLIPS_MK1, False)
            combo.note_event(NOTE_SHIFT_MK1, False)
            self.assertTrue(combo.poll_short())
            self.assertFalse(combo.poll_short())

    def test_releases_cancel_long_pending(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        combo.note_event(122, True)
        combo.note_event(119, True)
        self.assertTrue(combo.both_down)
        combo.note_event(122, False)
        self.assertFalse(combo.both_down)
        combo.note_event(119, False)
        self.assertFalse(combo.poll_long())
