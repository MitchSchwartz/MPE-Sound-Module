"""Tests for ClipMatrix Session View logic."""

from __future__ import annotations

import unittest

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.looper_engine import frames_to_bytes


class ClipMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        self.period_frames = 512
        self.period = bytes(frames_to_bytes(self.period_frames))

    def test_grid_record_and_play(self) -> None:
        self.matrix.on_grid(0, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        self.assertEqual(clip.state, ClipState.RECORDING)
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=self.period_frames)
        self.assertEqual(clip.state, ClipState.STOPPED)
        self.assertTrue(clip.has_content)
        self.matrix.on_grid(0, 0)
        self.assertEqual(clip.state, ClipState.PLAYING)
        out = self.matrix.process_period(bytes(frames_to_bytes(self.period_frames)), period_frames=self.period_frames)
        self.assertEqual(len(out), len(self.period))

    def test_scene_stops_row_at_bar(self) -> None:
        self.matrix.on_grid(0, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=self.period_frames)
        self.matrix.on_grid(0, 0)
        self.matrix.on_scene(0)
        self.assertEqual(clip.state, ClipState.STOPPING)
        self.matrix.process_period(self.period, period_frames=self.matrix.clock.frames_per_bar)
        self.assertEqual(clip.state, ClipState.STOPPED)

    def test_stop_all(self) -> None:
        self.matrix.on_grid(0, 1)
        clip = self.matrix.slot(0, 1)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=self.period_frames)
        self.matrix.on_grid(0, 1)
        self.matrix.on_stop_all()
        self.assertEqual(clip.state, ClipState.STOPPING)

    def test_inactive_slot_ignored(self) -> None:
        self.matrix.on_grid(1, 0)
        self.assertNotIn((1, 0), self.matrix.slots)


if __name__ == "__main__":
    unittest.main()
