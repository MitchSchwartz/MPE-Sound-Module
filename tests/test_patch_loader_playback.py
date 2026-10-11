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

    def test_load_patch_resolves_fx_policy_from_the_source_file(self) -> None:
        fx = mock.Mock()
        loader = PatchLoader(fx_policy=fx)
        loader.osc_client = FakeOscClient()
        loader.osc_enabled = True
        with (
            # Reuse Single hands back a cached copy; the flag is read from the source.
            mock.patch("patch_browser.patch_loader.ensure_reuse_single_patch", return_value=Path("/cache/Lead-abc.fxp")),
            mock.patch("patch_browser.patch_loader.time.sleep"),
            mock.patch.object(loader, "_capture_hold_baseline", return_value=True),
            mock.patch.object(loader, "_send_hold_osc", return_value=True),
            mock.patch.object(loader, "_apply_playback_policy"),
        ):
            self.assertTrue(loader.load_patch("/p/Lead.fxp", apply_normalization=False))
        fx.on_patch_loaded.assert_called_once_with("/p/Lead.fxp")

    def test_patch_mod_sends_surge_cc1(self) -> None:
        from patch_browser.fx_policy import FxPolicy

        loader = PatchLoader(fx_policy=FxPolicy(enabled=False, global_target="patch_first"))
        loader.osc_client = FakeOscClient()
        loader.osc_enabled = True
        self.assertTrue(loader.send_patch_mod(100))
        self.assertEqual(loader.osc_client.messages, [("/cc", [0.0, 1.0, 100.0])])


if __name__ == "__main__":
    unittest.main()
