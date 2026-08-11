"""Tests for ClipMatrix Session View logic."""

from __future__ import annotations

import unittest

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.looper_engine import bytes_to_frames, frames_to_bytes, quantize_loop_frames


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
        self.assertEqual(clip.state, ClipState.PLAYING)
        self.assertTrue(clip.has_content)
        out = self.matrix.process_period(bytes(frames_to_bytes(self.period_frames)), period_frames=self.period_frames)
        self.assertEqual(len(out), len(self.period))

    def test_early_stop_quantizes_loop_to_bars(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=4, loop_gain=1.0)
        period_frames = 512
        period = bytes(frames_to_bytes(period_frames))
        fpb = matrix.clock.frames_per_bar  # 96000 @ 120/48k

        matrix.on_grid(0, 0)
        clip = matrix.slot(0, 0)
        assert clip is not None
        half_bar = fpb // 2
        for _ in range(half_bar // period_frames):
            matrix.process_period(period, period_frames=period_frames)
        self.assertEqual(clip.state, ClipState.RECORDING)
        matrix.on_grid(0, 0)
        self.assertEqual(clip.state, ClipState.PLAYING)
        self.assertEqual(clip.loop_frames, fpb)

        clip.playback_frame = fpb - period_frames
        chunk = clip.ring.read_frames_for_loop(clip.playback_frame, period_frames, clip.loop_frames)
        self.assertEqual(bytes_to_frames(len(chunk)), period_frames)
        clip.playback_frame = (clip.playback_frame + period_frames) % clip.loop_frames
        self.assertEqual(clip.playback_frame, 0)

    def test_grid_playing_stops_immediately(self) -> None:
        self.matrix.on_grid(0, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=self.period_frames)
        self.matrix.on_grid(0, 0)
        self.assertEqual(clip.state, ClipState.STOPPED)
        out = self.matrix.process_period(self.period, period_frames=self.period_frames)
        self.assertEqual(out, self.period)

    def test_scene_stops_row_at_bar(self) -> None:
        self.matrix.on_grid(0, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=self.period_frames)
        self.assertEqual(clip.state, ClipState.PLAYING)
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
        self.assertEqual(clip.state, ClipState.PLAYING)
        self.matrix.on_stop_all()
        self.assertEqual(clip.state, ClipState.STOPPING)

    def test_inactive_slot_ignored(self) -> None:
        self.matrix.on_grid(1, 0)
        self.assertNotIn((1, 0), self.matrix.slots)


if __name__ == "__main__":
    unittest.main()
