"""Tests for global Surge output limiter OSC helpers."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.surge_output_limiter import (
    apply_output_limiter,
    disable_output_limiter,
    limiter_active,
    limiter_header_badge_label,
    sync_output_limiter,
)


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    def send_message(self, address: str, value) -> None:
        self.messages.append((address, value))


class SurgeOutputLimiterTests(unittest.TestCase):
    def test_apply_configures_conditioner_on_global_slot(self) -> None:
        osc = FakeOscClient()
        with mock.patch("patch_browser.surge_output_limiter.limiter_fx_slot", return_value=4):
            with mock.patch("patch_browser.surge_output_limiter.time.sleep"):
                self.assertTrue(apply_output_limiter(osc, threshold_db=-1.0))
        self.assertEqual(osc.messages[0], ("/param/fx/global/4/type", "Conditioner"))
        self.assertIn(("/param/fx/global/4/param5", -6.0), osc.messages)
        self.assertIn(("/param/fx/global/4/param8", -1.0), osc.messages)
        self.assertIn(("/param/fx/global/4/param1/enable", 0.0), osc.messages)
        self.assertIn(("/param/fx/global/4/param9", -60.0), osc.messages)
        self.assertEqual(osc.messages[-1], ("/param/fx/global/4/deactivate", 0.0))

    def test_disable_bypasses_slot(self) -> None:
        osc = FakeOscClient()
        with mock.patch("patch_browser.surge_output_limiter.limiter_fx_slot", return_value=4):
            self.assertTrue(disable_output_limiter(osc))
        self.assertEqual(osc.messages, [("/param/fx/global/4/deactivate", 1.0)])

    def test_sync_respects_pref(self) -> None:
        osc = FakeOscClient()
        with (
            mock.patch("patch_browser.surge_output_limiter.limiter_active", return_value=True),
            mock.patch("patch_browser.surge_output_limiter.apply_output_limiter", return_value=True) as apply_mock,
        ):
            sync_output_limiter(osc)
        apply_mock.assert_called_once_with(osc)

    def test_limiter_active_requires_pref_and_env(self) -> None:
        with (
            mock.patch("patch_browser.surge_output_limiter.limiter_enabled_by_env", return_value=True),
            mock.patch("patch_browser.surge_output_limiter.limiter_enabled_by_pref", return_value=False),
        ):
            self.assertFalse(limiter_active())

    def test_header_badge_hidden_when_inactive(self) -> None:
        with mock.patch("patch_browser.surge_output_limiter.limiter_active", return_value=False):
            self.assertIsNone(limiter_header_badge_label())

    def test_header_badge_shows_lim_when_active(self) -> None:
        with mock.patch("patch_browser.surge_output_limiter.limiter_active", return_value=True):
            self.assertEqual(limiter_header_badge_label(), "LIM")


if __name__ == "__main__":
    unittest.main()
