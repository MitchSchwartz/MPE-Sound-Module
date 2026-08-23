"""Tests for poly voice tracker and governor fade actuation."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.poly_voice_tracker import (
    PolyVoiceTracker,
    fade_actuation_enabled,
    read_active_voice_count,
    write_fade_request,
)
from patch_browser.surge_poly_governor import SurgePolyGovernor


class PolyVoiceTrackerTests(unittest.TestCase):
    def test_note_on_off_updates_count(self) -> None:
        tracker = PolyVoiceTracker()
        self.assertEqual(tracker.active_count(), 0)
        tracker.observe_message([0x90, 60, 100])
        self.assertEqual(tracker.active_count(), 1)
        tracker.observe_message([0x80, 60, 0])
        self.assertEqual(tracker.active_count(), 0)

    def test_notes_to_release_oldest_first(self) -> None:
        tracker = PolyVoiceTracker()
        with mock.patch("patch_browser.poly_voice_tracker.time.monotonic", side_effect=[1.0, 2.0, 3.0]):
            tracker.observe_message([0x90, 60, 100])
            tracker.observe_message([0x91, 62, 100])
        targets = tracker.notes_to_release(1)
        self.assertEqual(targets, [(0, 60)])

    def test_persist_and_read_active_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker_path = Path(tmp) / "poly-voice-tracker.json"
            tracker = PolyVoiceTracker()
            tracker.observe_message([0x90, 64, 100])
            with mock.patch("patch_browser.poly_voice_tracker.VOICE_TRACKER_FILE", tracker_path):
                tracker.persist()
                self.assertEqual(read_active_voice_count(), 1)


class GovernorFadeTests(unittest.TestCase):
    @mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True)
    @mock.patch("patch_browser.surge_poly_governor.fade_actuation_enabled", return_value=True)
    @mock.patch("patch_browser.surge_poly_governor.read_active_voice_count", return_value=8)
    @mock.patch("patch_browser.surge_poly_governor.send_polylimit")
    def test_step_down_defers_when_notes_sound(
        self,
        send_polylimit: mock.Mock,
        _active: mock.Mock,
        _fade: mock.Mock,
        _gov: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            state_path.write_text(
                json.dumps(
                    {
                        "patch": "Lead",
                        "native_poly": 16,
                        "ceiling_poly": 12,
                        "effective_poly": 12,
                        "reuse_single": True,
                    }
                ),
                encoding="utf-8",
            )
            osc = mock.Mock()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(
                osc,
                surge_monitor=monitor,
                cpu_monitor=mock.Mock(
                    snapshot=mock.Mock(
                        return_value={"online": True, "raw_percent": 55.0, "percent": 55.0}
                    )
                ),
            )
            with (
                mock.patch("patch_browser.surge_playback.POLY_STATE_FILE", state_path),
                mock.patch("patch_browser.surge_poly_governor.POLY_STATE_FILE", state_path),
            ):
                governor._last_patch = "Lead"
                governor._warm_preempt_done = True
                governor._high_since = time.monotonic() - 2.0
                governor._refresh_patch_state()
                with mock.patch("builtins.print"):
                    governor._tick()
            send_polylimit.assert_not_called()
            self.assertEqual(governor._pending_limit, 10)

    @mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True)
    @mock.patch("patch_browser.surge_poly_governor.fade_actuation_enabled", return_value=True)
    @mock.patch("patch_browser.surge_poly_governor.write_fade_request")
    @mock.patch("patch_browser.surge_poly_governor.read_active_voice_count", return_value=8)
    @mock.patch("patch_browser.surge_poly_governor.send_polylimit")
    def test_emergency_requests_fade_and_applies_limit(
        self,
        send_polylimit: mock.Mock,
        _active: mock.Mock,
        write_fade: mock.Mock,
        _fade: mock.Mock,
        _gov: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            state_path.write_text(
                json.dumps(
                    {
                        "patch": "Lead",
                        "native_poly": 16,
                        "ceiling_poly": 12,
                        "effective_poly": 9,
                        "reuse_single": True,
                    }
                ),
                encoding="utf-8",
            )
            osc = mock.Mock()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(
                osc,
                surge_monitor=monitor,
                cpu_monitor=mock.Mock(
                    snapshot=mock.Mock(
                        return_value={"online": True, "raw_percent": 92.0, "percent": 92.0}
                    )
                ),
            )
            with (
                mock.patch("patch_browser.surge_playback.POLY_STATE_FILE", state_path),
                mock.patch("patch_browser.surge_poly_governor.POLY_STATE_FILE", state_path),
            ):
                governor._last_patch = "Lead"
                governor._warm_preempt_done = True
                governor._refresh_patch_state()
                with mock.patch("builtins.print"):
                    governor._tick()
            write_fade.assert_called_once_with(release_count=6, reason="emergency")
            send_polylimit.assert_called_once()
            self.assertEqual(send_polylimit.call_args.args[1], 3)

    def test_fade_disabled_by_env(self) -> None:
        with mock.patch.dict(os.environ, {"MPE_POLY_GOVERNOR_FADE": "0"}, clear=False):
            self.assertFalse(fade_actuation_enabled())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(fade_actuation_enabled())


if __name__ == "__main__":
    unittest.main()
