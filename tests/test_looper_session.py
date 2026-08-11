"""Tests for interactive looper session state."""

from __future__ import annotations

import unittest

from patch_browser.control_surfaces.types import LooperTransportAction
from patch_browser.looper_engine import StereoRingBuffer, frames_to_bytes
from patch_browser.looper_session import LooperMode, LooperSession


class LooperSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = LooperSession(ring=StereoRingBuffer(4), loop_gain=1.0)

    def test_record_then_play(self) -> None:
        self.session.on_transport(LooperTransportAction.RECORD)
        self.assertEqual(self.session.mode, LooperMode.RECORDING)
        pcm = b"\x01\x00" * 4  # 2 stereo frames
        for _ in range(2):
            self.session.process_period(pcm)
        self.assertEqual(self.session.mode, LooperMode.PLAYING)
        out = self.session.output_pcm(pcm, period_frames=2)
        self.assertEqual(len(out), frames_to_bytes(2))

    def test_play_stop_toggles(self) -> None:
        pcm = b"\x01\x00" * 4
        self.session.on_transport(LooperTransportAction.RECORD)
        for _ in range(4):
            self.session.process_period(pcm)
        self.session.on_transport(LooperTransportAction.PLAY_STOP)
        self.assertEqual(self.session.mode, LooperMode.STOPPED)
        self.session.on_transport(LooperTransportAction.PLAY_STOP)
        self.assertEqual(self.session.mode, LooperMode.PLAYING)

    def test_clear_resets(self) -> None:
        self.session.on_transport(LooperTransportAction.RECORD)
        self.session.process_period(b"\x00\x00" * 4)
        self.session.on_transport(LooperTransportAction.CLEAR)
        self.assertEqual(self.session.mode, LooperMode.IDLE)
        self.assertEqual(self.session.ring.filled_frames, 0)


if __name__ == "__main__":
    unittest.main()
