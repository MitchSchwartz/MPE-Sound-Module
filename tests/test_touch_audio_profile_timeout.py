"""Tests for audio profile switch timeout recovery."""

from __future__ import annotations

import queue
import time
import unittest
from unittest import mock

if "pygame" not in __import__("sys").modules:
    __import__("sys").modules["pygame"] = mock.MagicMock()

from patch_browser.audio_profile import PROFILE_SWITCH_TIMEOUT_S
from patch_browser.touch_browser_prefs import TouchBrowserPrefsMixin


class _TimeoutHost(TouchBrowserPrefsMixin):
    def __init__(self) -> None:
        self._audio_profile_switching = True
        self._audio_profile_switch_target = "usb-host"
        self._audio_profile_switch_started = time.monotonic() - PROFILE_SWITCH_TIMEOUT_S - 10
        self._audio_profile_result_queue: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self._profile_switch_reload_active = True
        self._profile_switch_sent_once = False
        self.toast_message = ""

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message

    def _layout_settings_content(self) -> None:
        pass

    def _layout(self) -> None:
        pass


class AudioProfileSwitchTimeoutTests(unittest.TestCase):
    def test_stale_switch_unlocks_ui(self) -> None:
        host = _TimeoutHost()
        host._poll_audio_profile_switch()
        self.assertFalse(host._audio_profile_switching)
        self.assertIn("timed out", host.toast_message.lower())


if __name__ == "__main__":
    unittest.main()
