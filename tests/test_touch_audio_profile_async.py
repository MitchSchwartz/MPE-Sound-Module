"""Tests for async audio profile switch."""

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
        self._pending_last_patch = None
        self._profile_switch_reload_active = False
        self._profile_switch_sent_once = False
        self._last_known_surge_pid = None
        self._surge_was_healthy = False
        self._surge_liveness_initialized = False
        self.surge_monitor = mock.Mock()
        self.surge_monitor.last_check_time = 0.0
        self.scanner = mock.Mock()
        self.loaded_patch_info = None
        self.toast_message = ""
        self.toast_until = 0.0
        self._queued_reload: tuple[dict, float] | None = None
        self.screen_state = None

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message

    def _queue_patch_reload(self, patch: dict, *, delay_s: float = 2.0) -> None:
        self._queued_reload = (dict(patch), delay_s)

    def _layout_settings_content(self) -> None:
        pass

    def _layout(self) -> None:
        pass

    def _close_audio_profile_modal(self) -> None:
        pass


class AsyncAudioProfileTests(unittest.TestCase):
    @mock.patch("patch_browser.audio_profile.apply_profile")
    @mock.patch("patch_browser.audio_profile.current_profile", return_value="standalone")
    def test_begin_switch_starts_background(self, _current: mock.Mock, apply_mock: mock.Mock) -> None:
        host = _AudioHost()
        apply_mock.return_value = (True, "Session record — mic → USB when PC captures")

        host._begin_audio_profile_switch("usb-host-session")
        self.assertTrue(host._audio_profile_switching)
        self.assertEqual(host._audio_profile_switch_target, "usb-host-session")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            host._poll_audio_profile_switch()
            if not host._audio_profile_switching:
                break
            time.sleep(0.02)

        self.assertFalse(host._audio_profile_switching)
        apply_mock.assert_called_once_with("usb-host-session")

    @mock.patch("patch_browser.audio_profile.apply_profile")
    @mock.patch("patch_browser.audio_profile.current_profile", return_value="standalone")
    def test_double_switch_ignored_while_busy(self, _current: mock.Mock, apply_mock: mock.Mock) -> None:
        host = _AudioHost()
        started = threading.Event()

        def slow_apply(_profile: str) -> tuple[bool, str]:
            started.set()
            time.sleep(0.3)
            return True, "ok"

        apply_mock.side_effect = slow_apply
        host._begin_audio_profile_switch("usb-host")
        self.assertTrue(started.wait(timeout=1.0))
        host._begin_audio_profile_switch("standalone")
        self.assertEqual(apply_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
