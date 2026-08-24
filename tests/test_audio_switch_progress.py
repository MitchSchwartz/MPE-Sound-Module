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


if __name__ == "__main__":
    unittest.main()
