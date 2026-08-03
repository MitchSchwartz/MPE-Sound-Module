"""Tests for PatchLoader playback policy integration."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from patch_browser.patch_loader import PatchLoader


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    def send_message(self, address: str, value) -> None:
        self.messages.append((address, value))


class PatchLoaderPlaybackTests(unittest.TestCase):
    def test_load_patch_applies_playback_policy(self) -> None:
        loader = PatchLoader()
        loader.osc_client = FakeOscClient()
        loader.osc_enabled = True
        with (
            mock.patch("patch_browser.patch_loader.ensure_reuse_single_patch", return_value=Path("/p/Lead.fxp")),
            mock.patch("patch_browser.patch_loader.time.sleep"),
            mock.patch.object(loader, "_capture_hold_baseline", return_value=True),
            mock.patch.object(loader, "_send_hold_osc", return_value=True),
            mock.patch.object(loader, "_apply_playback_policy") as policy_mock,
        ):
            ok = loader.load_patch("/p/Lead.fxp", apply_normalization=False)
        self.assertTrue(ok)
        policy_mock.assert_called_once_with("Lead")


if __name__ == "__main__":
    unittest.main()
