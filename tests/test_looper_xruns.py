"""Tests for ALSA PCM state parsing.

The removed `read_xrun_counts` used to be tested against a fabricated
`xruns: 3` status file. /proc/asound has no such field, so the test passed
while the function returned 0 on every real device. Only parse fields the
kernel actually writes here; underrun counting lives in test_looper_alsa_stderr.
"""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.looper_xruns import any_pcm_xrun_state, read_pcm_states


class FakePath:
    def __init__(self, text: str, path: str) -> None:
        self._text = text
        self._path = path

    def read_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self._text

    def __str__(self) -> str:
        return self._path


# Shape of a real Pi status file — note the absence of any cumulative counter.
_RUNNING = (
    "state: RUNNING\n"
    "owner_pid   : 1234\n"
    "trigger_time: 4210.123456789\n"
    "tstamp      : 4215.987654321\n"
    "delay       : 512\n"
    "avail       : 0\n"
    "avail_max   : 1024\n"
    "-----\n"
    "hw_ptr      : 2048\n"
    "appl_ptr    : 2560\n"
)


class LooperPcmStateTests(unittest.TestCase):
    @mock.patch("patch_browser.looper_xruns.list_pcm_status_files")
    def test_read_pcm_states(self, list_mock: mock.Mock) -> None:
        list_mock.return_value = [
            FakePath(_RUNNING, "/proc/asound/card0/pcm0p/sub0/status"),
            FakePath("state: XRUN\ndelay : 0\n", "/proc/asound/card1/pcm0c/sub0/status"),
        ]
        states = read_pcm_states()
        self.assertEqual(states["/proc/asound/card0/pcm0p/sub0/status"], "RUNNING")
        self.assertEqual(states["/proc/asound/card1/pcm0c/sub0/status"], "XRUN")

    @mock.patch("patch_browser.looper_xruns.list_pcm_status_files")
    def test_any_pcm_xrun_state_reports_only_xrun(self, list_mock: mock.Mock) -> None:
        list_mock.return_value = [
            FakePath(_RUNNING, "/proc/asound/card0/pcm0p/sub0/status"),
            FakePath("state: XRUN\n", "/proc/asound/card1/pcm0c/sub0/status"),
        ]
        self.assertEqual(any_pcm_xrun_state(), ["/proc/asound/card1/pcm0c/sub0/status"])

    def test_closed_stream_is_not_reported_as_xrun(self) -> None:
        states = {"/proc/asound/card0/pcm0p/sub0/status": "SETUP"}
        self.assertEqual(any_pcm_xrun_state(states), [])


if __name__ == "__main__":
    unittest.main()
