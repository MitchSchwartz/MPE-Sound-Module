"""Tests for touch UI audio-switch progress hints and cooldown toasts."""

from __future__ import annotations

import unittest

from patch_browser.audio_engine import COOLDOWN_SEC, audio_switch_progress_message


class AudioSwitchProgressTests(unittest.TestCase):
    def test_ok_state(self) -> None:
        hint, toast, sec = audio_switch_progress_message(
            {"state": "ok", "active": "jack"},
            now=1000,
        )
        self.assertEqual(hint, "Audio restored")
        self.assertEqual(toast, "Audio ready")
        self.assertEqual(sec, 2.0)

    def test_recovering_during_cooldown_shows_reconnecting_not_paused(self) -> None:
        now = 1000
        hint, toast, sec = audio_switch_progress_message(
            {"state": "recovering", "active": "jack", "reason": "promote-to-jack"},
            {"last_restart": str(now - 12), "restarts": "1"},
            now=now,
        )
        self.assertIn("Reconnect", hint)
        self.assertIn("Reconnect", toast or "")
        self.assertNotIn("paused", (toast or "").lower())
        self.assertEqual(sec, 3.0)

    def test_settings_change_hint(self) -> None:
        hint, toast, _ = audio_switch_progress_message(
            {"state": "recovering", "reason": "settings-change"},
            now=1000,
        )
        self.assertIn("JACK", hint)
        self.assertEqual(toast, "Applying audio settings…")

    def test_failed_supervisor_exhausted(self) -> None:
        hint, toast, _ = audio_switch_progress_message(
            {"state": "failed", "reason": "supervisor-exhausted"},
            now=1000,
        )
        self.assertIn("paused", hint.lower())
        self.assertIn(str(COOLDOWN_SEC), toast or "")

    def test_failed_in_cooldown_shows_retry_wait(self) -> None:
        now = 1000
        hint, toast, _ = audio_switch_progress_message(
            {"state": "failed", "reason": "surge-failed"},
            {"last_restart": str(now - 12), "restarts": "2"},
            now=now,
        )
        remaining = COOLDOWN_SEC - 12
        self.assertIn(f"{remaining}s", hint)
        self.assertIn("paused", (toast or "").lower())

    def test_no_device_recovering_does_not_promise_reconnection(self) -> None:
        """Nothing plugged in -> nothing to reconnect TO."""
        hint, toast, _ = audio_switch_progress_message(
            {"state": "recovering", "reason": "no-device"},
            now=1000,
        )
        self.assertIn("no audio device", hint.lower())
        self.assertNotIn("reconnect", hint.lower())
        self.assertNotIn("reconnect", (toast or "").lower())

    def test_no_device_failed_matches_recovering(self) -> None:
        """Same cause, same message — the user cannot act on the distinction."""
        rec, rec_toast, _ = audio_switch_progress_message(
            {"state": "recovering", "reason": "no-device"}, now=1000
        )
        failed, failed_toast, _ = audio_switch_progress_message(
            {"state": "failed", "reason": "no-device"}, now=1000
        )
        self.assertEqual(rec, failed)
        self.assertEqual(rec_toast, failed_toast)

    def test_no_device_has_no_loader_ellipsis(self) -> None:
        """toast_loader_base() strips '…' to drive the animated loader.

        Waiting on the user is a resting state; animating progress that cannot
        occur misreports what the appliance is doing.
        """
        _, toast, _ = audio_switch_progress_message(
            {"state": "recovering", "reason": "no-device"}, now=1000
        )
        self.assertFalse((toast or "").endswith("…"))

    def test_unusable_device_differs_from_absent_device(self) -> None:
        """A present-but-unusable card is not fixed by plugging something in."""
        absent, _, _ = audio_switch_progress_message(
            {"state": "failed", "reason": "no-device"}, now=1000
        )
        unusable, _, _ = audio_switch_progress_message(
            {"state": "failed", "reason": "no-card-resolved"}, now=1000
        )
        self.assertNotEqual(absent, unusable)

    def test_toast_loader_base_strips_ellipsis(self) -> None:
        from patch_browser.audio_engine import toast_loader_base

        self.assertEqual(toast_loader_base("Reconnecting audio…"), "Reconnecting audio")
        self.assertEqual(toast_loader_base("Connecting keyboard..."), "Connecting keyboard")

    def test_loader_dot_count_cycles(self) -> None:
        from patch_browser.audio_engine import LOADER_DOT_WIDTH, loader_dot_count

        self.assertEqual(loader_dot_count(tick=0), 1)
        self.assertEqual(loader_dot_count(tick=2), 3)
        self.assertEqual(loader_dot_count(tick=3), 1)
        self.assertEqual(loader_dot_count(tick=0, width=LOADER_DOT_WIDTH), 1)


if __name__ == "__main__":
    unittest.main()
