"""Tests for ClipMatrix Session View logic."""

from __future__ import annotations

import unittest

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.looper_bar_clock import LooperBarClock
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

    def test_playback_mutes_live_monitor(self) -> None:
        import struct

        record_pcm = struct.pack("<hh", 10000, 10000) * self.period_frames
        loud_live = struct.pack("<hh", 20000, 20000) * self.period_frames
        silent = bytes(frames_to_bytes(self.period_frames))
        self.matrix.on_grid(0, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            self.matrix.process_period(record_pcm, period_frames=self.period_frames)
        self.assertEqual(clip.state, ClipState.PLAYING)
        out_silent = self.matrix.process_period(silent, period_frames=self.period_frames)
        out_loud = self.matrix.process_period(loud_live, period_frames=self.period_frames)
        self.assertEqual(out_silent, out_loud)
        self.assertNotEqual(out_loud, loud_live)

    def test_overdub_keeps_live_monitor(self) -> None:
        import struct

        live = struct.pack("<hh", 5000, 5000) * self.period_frames
        self.matrix.on_grid(0, 0)
        clip_a = self.matrix.slot(0, 0)
        assert clip_a is not None
        while clip_a.state == ClipState.RECORDING:
            self.matrix.process_period(live, period_frames=self.period_frames)
        self.matrix.on_grid(0, 1)
        clip_b = self.matrix.slot(0, 1)
        assert clip_b is not None
        self.assertEqual(clip_b.state, ClipState.RECORDING)
        out = self.matrix.process_period(live, period_frames=self.period_frames)
        self.assertNotEqual(out, b"\x00" * len(out))


class ClipMatrixTransportOriginTests(unittest.TestCase):
    """Bar 1 beat 1 must coincide with the first recorded frame (idle → active)."""

    def setUp(self) -> None:
        self.matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        self.period_frames = 512
        self.period = bytes(frames_to_bytes(self.period_frames))

    def _idle_for(self, periods: int, matrix: ClipMatrix | None = None) -> None:
        target = self.matrix if matrix is None else matrix
        for _ in range(periods):
            target.process_period(self.period, period_frames=self.period_frames)

    def _record_to_playing(self, matrix: ClipMatrix, row: int, col: int):
        matrix.on_grid(row, col)
        clip = matrix.slot(row, col)
        assert clip is not None
        while clip.state == ClipState.RECORDING:
            matrix.process_period(self.period, period_frames=self.period_frames)
        return clip

    def test_first_grid_press_zeroes_free_running_clock(self) -> None:
        self._idle_for(324)  # ~1.73 bars of idle drift, as measured on the Pi
        self.assertGreater(self.matrix.clock.total_frames, 0)
        self.matrix.on_grid(0, 0)
        self.assertEqual(self.matrix.clock.total_frames, 0)
        clip = self.matrix.slot(0, 0)
        assert clip is not None
        self.assertEqual(clip.state, ClipState.RECORDING)

    def test_second_clip_does_not_rezero_mid_session(self) -> None:
        self.matrix.on_grid(0, 0)
        self._idle_for(4)
        recording_frames = self.matrix.clock.total_frames
        self.assertGreater(recording_frames, 0)
        self.matrix.on_grid(0, 1)
        self.assertEqual(self.matrix.clock.total_frames, recording_frames)

        clip_a = self.matrix.slot(0, 0)
        assert clip_a is not None
        while clip_a.state == ClipState.RECORDING:
            self.matrix.process_period(self.period, period_frames=self.period_frames)
        playing_frames = self.matrix.clock.total_frames
        self.matrix.on_grid(0, 2)
        self.assertEqual(self.matrix.clock.total_frames, playing_frames)

    def test_stopping_a_playing_clip_does_not_reset(self) -> None:
        clip = self._record_to_playing(self.matrix, 0, 0)
        self.assertEqual(clip.state, ClipState.PLAYING)
        before = self.matrix.clock.total_frames
        self.matrix.on_grid(0, 0)
        self.assertEqual(clip.state, ClipState.STOPPED)
        self.assertEqual(self.matrix.clock.total_frames, before)

    def test_ring_fill_during_process_period_does_not_reset(self) -> None:
        clip = self._record_to_playing(self.matrix, 0, 0)
        self.assertEqual(clip.state, ClipState.PLAYING)
        self.assertGreaterEqual(self.matrix.clock.total_frames, self.matrix.loop_frames)

    def test_scene_launch_resets_only_from_idle(self) -> None:
        matrix = ClipMatrix(
            clock=LooperBarClock(sample_rate=48000, bpm=120.0, beats_per_bar=4, bars_per_loop=1),
            loop_frames=96000,
            enabled_slots=frozenset({(0, 0), (1, 0)}),
        )
        stopped = self._record_to_playing(matrix, 1, 0)
        matrix.on_grid(1, 0)
        self.assertEqual(stopped.state, ClipState.STOPPED)
        self.assertTrue(stopped.has_content)

        self._idle_for(37, matrix)
        self.assertGreater(matrix.clock.total_frames, 0)
        matrix.on_scene(1)
        self.assertEqual(stopped.state, ClipState.PLAYING)
        self.assertEqual(matrix.clock.total_frames, 0)

        playing = self._record_to_playing(matrix, 0, 0)
        self.assertEqual(playing.state, ClipState.PLAYING)
        matrix.on_grid(1, 0)  # park row 1 as STOPPED while row 0 keeps playing
        self.assertEqual(stopped.state, ClipState.STOPPED)
        before = matrix.clock.total_frames
        matrix.on_scene(1)
        self.assertEqual(stopped.state, ClipState.PLAYING)
        self.assertEqual(matrix.clock.total_frames, before)


if __name__ == "__main__":
    unittest.main()
