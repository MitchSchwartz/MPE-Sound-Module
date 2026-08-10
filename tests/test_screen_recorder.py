"""Tests for patch_browser.screen_recorder."""

from __future__ import annotations

import errno
import os
import sys
import threading
import unittest
from unittest import mock

from patch_browser.screen_recorder import ScreenRecorder


class _FakeSurface:
    def __init__(self, size: tuple[int, int]) -> None:
        self._size = size

    def get_size(self) -> tuple[int, int]:
        return self._size


class ScreenRecorderTests(unittest.TestCase):
    def test_from_env_disabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ScreenRecorder.from_env())

    def test_from_env_enabled(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "MPE_SCREEN_RECORD": "1",
                "MPE_SCREEN_RECORD_PIPE": "/tmp/test.pipe",
                "MPE_SCREEN_RECORD_FPS": "24",
            },
            clear=False,
        ):
            rec = ScreenRecorder.from_env()
            self.assertIsNotNone(rec)
            assert rec is not None
            self.assertTrue(rec.active)
            self.assertEqual(rec._fps, 24)

    def test_write_frame_throttles_by_fps(self) -> None:
        rec = ScreenRecorder(
            enabled=True,
            pipe_path="/tmp/nope",
            fps=10,
            width=800,
            height=480,
        )
        rec._fd = 999
        rec._last_frame_at = -1.0
        surface = _FakeSurface((800, 480))
        with mock.patch.dict(sys.modules, {"pygame": mock.MagicMock()}):
            fake_pygame = sys.modules["pygame"]
            fake_pygame.image.tostring.return_value = b"x" * (800 * 480 * 3)
            with mock.patch("os.write") as write_mock:
                rec.write_frame(surface, now=0.0)
                rec.write_frame(surface, now=0.01)
                self.assertEqual(write_mock.call_count, 1)
                fake_pygame.image.tostring.assert_called_once()

    def test_write_frame_size_mismatch_disables(self) -> None:
        rec = ScreenRecorder(
            enabled=True,
            pipe_path="/tmp/nope",
            fps=30,
            width=800,
            height=480,
        )
        rec._fd = 999
        rec._last_frame_at = -1.0
        rec.write_frame(_FakeSurface((640, 480)), now=0.0)
        self.assertFalse(rec.active)

    def test_fifo_round_trip(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pipe_path = os.path.join(tmp, "rec.pipe")
            os.mkfifo(pipe_path)
            rec = ScreenRecorder(
                enabled=True,
                pipe_path=pipe_path,
                fps=30,
                width=2,
                height=2,
            )
            payload = b"\x01\x02\x03" * 4
            surface = _FakeSurface((2, 2))
            received: list[bytes] = []

            def reader() -> None:
                with open(pipe_path, "rb", buffering=0) as fh:
                    received.append(fh.read(len(payload)))

            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            with mock.patch.dict(sys.modules, {"pygame": mock.MagicMock()}):
                sys.modules["pygame"].image.tostring.return_value = payload
                rec._last_frame_at = -1.0
                rec.write_frame(surface, now=0.0)
            thread.join(timeout=2.0)
            rec.close()
            self.assertEqual(received, [payload])

    def test_enxio_waits_for_reader(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pipe_path = os.path.join(tmp, "rec.pipe")
            os.mkfifo(pipe_path)
            rec = ScreenRecorder(
                enabled=True,
                pipe_path=pipe_path,
                fps=30,
                width=800,
                height=480,
            )
            with mock.patch("os.open", side_effect=OSError(errno.ENXIO, "no reader")):
                self.assertFalse(rec._ensure_open())
            self.assertTrue(rec.active)


if __name__ == "__main__":
    unittest.main()
