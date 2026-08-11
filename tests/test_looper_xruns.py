"""Tests for ALSA xrun proc parsing."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.looper_xruns import read_xrun_counts, total_xruns


class LooperXrunTests(unittest.TestCase):
    @mock.patch("patch_browser.looper_xruns.list_pcm_status_files")
    def test_read_xrun_counts(self, list_mock: mock.Mock) -> None:
        class FakePath:
            def __init__(self, text: str, path: str) -> None:
                self._text = text
                self._path = path

            def read_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
                return self._text

            def __str__(self) -> str:
                return self._path

        list_mock.return_value = [
            FakePath("state: RUNNING\nxruns: 3\n", "/proc/asound/card0/pcm0p/sub0/status"),
            FakePath("state: RUNNING\n", "/proc/asound/card1/pcm0c/sub0/status"),
        ]
        counts = read_xrun_counts()
        self.assertEqual(counts["/proc/asound/card0/pcm0p/sub0/status"], 3)
        self.assertEqual(total_xruns(counts), 3)


if __name__ == "__main__":
    unittest.main()
