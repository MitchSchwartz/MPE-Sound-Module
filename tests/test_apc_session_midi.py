"""Tests for APC Session View MIDI (shift+stop-all hold clear)."""

from __future__ import annotations

import unittest

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.control_surfaces import get_apc_map
from patch_browser.control_surfaces.apc_session_midi import (
    ApcMidiContext,
    CLEAR_SESSION_HOLD_S,
    check_clear_session_hold,
    handle_apc_session_message,
)
from patch_browser.looper_engine import frames_to_bytes


def _note_on(note: int, *, velocity: int = 127) -> list[int]:
    return [0x90, note, velocity]


def _note_off(note: int) -> list[int]:
    return [0x80, note, 0]


class ApcSessionMidiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.surface = get_apc_map()
        self.matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        self.ctx = ApcMidiContext()
        self.period = bytes(frames_to_bytes(512))
        shift = self.surface.shift_note
        assert shift is not None
        self.shift = shift
        self.scene8 = self.surface.scene_launch_notes[7]

    def test_shift_scene8_tap_stops_without_clear(self) -> None:
        self.matrix.on_grid(0, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=512)
        self.ctx.shift_held = True
        handle_apc_session_message(
            self.surface,
            _note_on(self.scene8),
            self.ctx,
            self.matrix,
            now=0.0,
        )
        self.assertEqual(clip.state, ClipState.STOPPING)
        cleared = check_clear_session_hold(self.ctx, self.matrix, now=1.0)
        self.assertFalse(cleared)
        self.assertTrue(clip.has_content)

    def test_shift_scene8_hold_clears_session(self) -> None:
        self.matrix.on_grid(0, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=512)
        self.ctx.shift_held = True
        handle_apc_session_message(
            self.surface,
            _note_on(self.scene8),
            self.ctx,
            self.matrix,
            now=0.0,
        )
        cleared = check_clear_session_hold(
            self.ctx,
            self.matrix,
            now=CLEAR_SESSION_HOLD_S,
        )
        self.assertTrue(cleared)
        self.assertEqual(clip.state, ClipState.EMPTY)
        self.assertFalse(clip.has_content)


if __name__ == "__main__":
    unittest.main()
