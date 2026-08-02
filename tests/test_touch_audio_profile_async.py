"""Tests for async audio profile toggle."""

from __future__ import annotations

import queue
import threading
import time
import unittest
from unittest import mock

if "pygame" not in __import__("sys").modules:
    __import__("sys").modules["pygame"] = mock.MagicMock()

from patch_browser.touch_browser_prefs import TouchBrowserPrefsMixin


class _AudioHost(TouchBrowserPrefsMixin):
    def __init__(self) -> None:
        self._audio_profile_switching = False
        self._audio_profile_switch_target = None
        self._audio_profile_switch_started = 0.0
        self._audio_profile_result_queue: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self.loaded_patch_info = None
        self.toast_message = ""
        self.toast_until = 0.0

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message

    def _layout_settings_content(self) -> None:
        pass

    def _layout(self) -> None:
        pass


class AsyncAudioProfileTests(unittest.TestCase):
    @mock.patch("patch_browser.audio_profile.apply_profile")
    @mock.patch("patch_browser.audio_profile.current_profile", return_value="standalone")
    def test_toggle_starts_background_switch(self, _current: mock.Mock, apply_mock: mock.Mock) -> None:
        host = _AudioHost()
        apply_mock.return_value = (True, "USB host audio — plug USB-C to PC")

        host._toggle_audio_profile()
        self.assertTrue(host._audio_profile_switching)
        self.assertEqual(host._audio_profile_switch_target, "usb-host")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            host._poll_audio_profile_switch()
            if not host._audio_profile_switching:
                break
            time.sleep(0.02)

        self.assertFalse(host._audio_profile_switching)
        apply_mock.assert_called_once_with("usb-host")

    @mock.patch("patch_browser.audio_profile.apply_profile")
    @mock.patch("patch_browser.audio_profile.current_profile", return_value="standalone")
    def test_double_toggle_ignored_while_busy(self, _current: mock.Mock, apply_mock: mock.Mock) -> None:
        host = _AudioHost()
        started = threading.Event()

        def slow_apply(_profile: str) -> tuple[bool, str]:
            started.set()
            time.sleep(0.3)
            return True, "ok"

        apply_mock.side_effect = slow_apply
        host._toggle_audio_profile()
        self.assertTrue(started.wait(timeout=1.0))
        host._toggle_audio_profile()
        self.assertEqual(apply_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
