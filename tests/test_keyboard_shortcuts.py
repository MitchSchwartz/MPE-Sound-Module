"""Chord table for the touch browser (#113 Phase 0)."""

from __future__ import annotations

import unittest

from patch_browser.keyboard_shortcuts import (
    ACTION_RESTART_BENCH,
    ACTION_TERMINAL,
    KMOD_LALT,
    KMOD_LCTRL,
    KMOD_RALT,
    KMOD_RCTRL,
    K_R,
    K_T,
    chord_label,
    match_chord,
)

CTRL_ALT = KMOD_LCTRL | KMOD_LALT


class TestMatchChord(unittest.TestCase):
    def test_ctrl_alt_t_opens_terminal(self) -> None:
        self.assertEqual(match_chord(K_T, CTRL_ALT), ACTION_TERMINAL)

    def test_ctrl_alt_r_runs_restart_bench(self) -> None:
        self.assertEqual(match_chord(K_R, CTRL_ALT), ACTION_RESTART_BENCH)

    def test_right_hand_modifiers_work_too(self) -> None:
        self.assertEqual(match_chord(K_T, KMOD_RCTRL | KMOD_RALT), ACTION_TERMINAL)

    def test_one_modifier_is_not_enough(self) -> None:
        """Two modifiers are the guard against a stray key from a controller
        that enumerates as a keyboard firing a stack restart mid-set."""
        self.assertIsNone(match_chord(K_T, KMOD_LCTRL))
        self.assertIsNone(match_chord(K_T, KMOD_LALT))
        self.assertIsNone(match_chord(K_T, 0))

    def test_extra_modifiers_are_tolerated(self) -> None:
        """Caps lock held must not silently break the chord."""
        self.assertEqual(match_chord(K_T, CTRL_ALT | 0x2000), ACTION_TERMINAL)

    def test_unmapped_key_is_none(self) -> None:
        self.assertIsNone(match_chord(ord("q"), CTRL_ALT))

    def test_chord_label(self) -> None:
        self.assertEqual(chord_label(ACTION_TERMINAL), "Ctrl+Alt+T")
        self.assertEqual(chord_label(ACTION_RESTART_BENCH), "Ctrl+Alt+R")
        self.assertEqual(chord_label("nope"), "")


class TestConstantsMatchPygame(unittest.TestCase):
    """The constants are hardcoded SDL values. Where real pygame is installed,
    prove they still agree — a silent divergence would make every chord dead."""

    def test_against_real_pygame(self) -> None:
        import sys
        from unittest import mock

        real = sys.modules.get("pygame")
        if real is None or isinstance(real, mock.MagicMock) or not hasattr(real, "K_t"):
            self.skipTest("real pygame not installed")
        self.assertEqual(K_T, real.K_t)
        self.assertEqual(K_R, real.K_r)
        self.assertEqual(KMOD_LCTRL, real.KMOD_LCTRL)
        self.assertEqual(KMOD_LALT, real.KMOD_LALT)


if __name__ == "__main__":
    unittest.main()
