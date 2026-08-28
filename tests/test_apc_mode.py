"""APC mini mk2 mode-change SysEx decoding."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from apc_mode import (  # noqa: E402
    MODE_NOTES,
    ApcMode,
    grid_silent_reason,
    parse_mode_sysex,
)

# Captured verbatim from the device, 2026-08-28, on entering Notes mode.
NOTES_SYSEX = [0xF0, 0x47, 0x7F, 0x4F, 0x62, 0x00, 0x01, 0x01, 0xF7]


def sysex(mode: int) -> list[int]:
    return [0xF0, 0x47, 0x7F, 0x4F, 0x62, 0x00, 0x01, mode, 0xF7]


class Decoding(unittest.TestCase):
    def test_captured_notes_mode_message(self):
        mode = parse_mode_sysex(NOTES_SYSEX)
        self.assertIsNotNone(mode)
        self.assertEqual(mode.value, MODE_NOTES)
        self.assertEqual(mode.label, "Notes")
        self.assertTrue(mode.confirmed)
        self.assertFalse(mode.grid_available)

    def test_trailing_f7_may_be_absent(self):
        self.assertEqual(parse_mode_sysex(NOTES_SYSEX[:-1]), parse_mode_sysex(NOTES_SYSEX))

    def test_undecoded_modes_are_reported_as_unknown_not_guessed(self):
        """0x00 and 0x02 were observed but never tied to a mode. Labelling
        them would be worse than not: a wrong label would tell Mitch the
        grid should work when it cannot."""
        for value in (0x00, 0x02):
            mode = parse_mode_sysex(sysex(value))
            self.assertIsNotNone(mode)
            self.assertFalse(mode.confirmed)
            self.assertIn("unknown", mode.describe())
            self.assertIn(f"0x{value:02X}", mode.describe())

    def test_unknown_mode_does_not_claim_the_grid_is_dead(self):
        """Only Notes mode is proven to silence the grid."""
        self.assertTrue(parse_mode_sysex(sysex(0x02)).grid_available)
        self.assertIsNone(grid_silent_reason(parse_mode_sysex(sysex(0x02))))


class Rejection(unittest.TestCase):
    def test_non_sysex_is_not_a_mode(self):
        for msg in ([0x90, 60, 127], [0x80, 60, 0], [0xB0, 48, 100], []):
            self.assertIsNone(parse_mode_sysex(msg), msg)

    def test_other_vendors_and_products_are_rejected(self):
        wrong_vendor = sysex(1)
        wrong_vendor[1] = 0x00
        self.assertIsNone(parse_mode_sysex(wrong_vendor))
        wrong_product = sysex(1)
        wrong_product[3] = 0x28
        self.assertIsNone(parse_mode_sysex(wrong_product))

    def test_other_akai_message_types_are_rejected(self):
        other = sysex(1)
        other[4] = 0x61
        self.assertIsNone(parse_mode_sysex(other))

    def test_truncated_and_overlong_messages_are_rejected(self):
        self.assertIsNone(parse_mode_sysex([0xF0, 0x47, 0x7F]))
        self.assertIsNone(parse_mode_sysex(sysex(1) + [0x00]))

    def test_wrong_payload_length_field_is_rejected(self):
        bad = sysex(1)
        bad[5], bad[6] = 0x00, 0x02
        self.assertIsNone(parse_mode_sysex(bad))


class Explanation(unittest.TestCase):
    def test_notes_mode_explains_the_dead_grid(self):
        reason = grid_silent_reason(parse_mode_sysex(NOTES_SYSEX))
        self.assertIsNotNone(reason)
        self.assertIn("Notes mode", reason)
        self.assertIn("Shift", reason, "must say how to get back")

    def test_no_reason_when_no_mode_seen(self):
        self.assertIsNone(grid_silent_reason(None))


if __name__ == "__main__":
    unittest.main()
