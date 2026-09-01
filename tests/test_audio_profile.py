"""Tests for MPE audio profile helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import audio_profile


class AudioProfileTests(unittest.TestCase):
    def test_normalize_profile_defaults_unknown(self) -> None:
        self.assertEqual(audio_profile.normalize_profile(None), "standalone")
        self.assertEqual(audio_profile.normalize_profile("garbage"), "standalone")

    def test_normalize_profile_usb_host_session(self) -> None:
        self.assertEqual(audio_profile.normalize_profile("usb-host-session"), "usb-host-session")

    def test_profile_option_labels(self) -> None:
        self.assertEqual(audio_profile.profile_option_label("standalone"), "Analog")
        self.assertEqual(audio_profile.profile_option_label("usb-host-session"), "USB session")

    @mock.patch.dict(
        os.environ,
        {"MPE_ENV_FILE": "", "MPE_AUDIO_PROFILE": "usb-host-session"},
        clear=False,
    )
    def test_header_badge_usb_session(self) -> None:
        self.assertEqual(audio_profile.header_badge_label(), "USB")

    @mock.patch.dict(
        os.environ,
        {"MPE_ENV_FILE": "", "MPE_AUDIO_PROFILE": "usb-host"},
        clear=False,
    )
    def test_header_badge_usb(self) -> None:
        self.assertEqual(audio_profile.header_badge_label(), "USB")
        self.assertTrue(audio_profile.settings_toggle_on())

    @mock.patch.dict(
        os.environ,
        {"MPE_ENV_FILE": "", "MPE_AUDIO_PROFILE": "standalone"},
        clear=False,
    )
    def test_header_badge_analog(self) -> None:
        self.assertEqual(audio_profile.header_badge_label(), "Analog")
        self.assertFalse(audio_profile.settings_toggle_on())

    def test_read_profile_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mpe.env"
            path.write_text("# comment\nMPE_AUDIO_PROFILE=usb-host\n", encoding="utf-8")
            self.assertEqual(audio_profile.read_profile_from_env_file(path), "usb-host")

    @mock.patch("patch_browser.audio_profile.subprocess.run")
    @mock.patch.dict(os.environ, {"MPE_AUDIO_PROFILE": "standalone"}, clear=False)
    def test_apply_profile_success(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        ok, message = audio_profile.apply_profile("usb-host")
        self.assertTrue(ok)
        self.assertIn("USB", message)
        self.assertEqual(os.environ["MPE_AUDIO_PROFILE"], "usb-host")
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0][2], "usb-host")



class MenuHidesSessionProfileTests(unittest.TestCase):
    """usb-host-session is off the menu, but still a profile the appliance knows.

    Hiding it by deleting the PROFILE_OPTIONS row would make
    profile_option_label() fall through to "Analog", so an appliance sitting on
    the hidden profile would report itself as a different one. That is the
    recurring defect in this area, not a cosmetic detail.
    """

    def test_menu_omits_the_hidden_profile(self) -> None:
        keys = [key for key, _t, _s in audio_profile.menu_profile_options("standalone")]
        self.assertNotIn("usb-host-session", keys)
        self.assertIn("standalone", keys)
        self.assertIn("usb-host", keys)

    def test_hidden_profile_reappears_when_it_is_the_active_one(self) -> None:
        """Otherwise the menu cannot name the current state or switch off it."""
        keys = [key for key, _t, _s in audio_profile.menu_profile_options("usb-host-session")]
        self.assertIn("usb-host-session", keys)

    def test_the_label_survives_being_hidden(self) -> None:
        self.assertEqual(audio_profile.profile_option_label("usb-host-session"), "USB session")

    def test_the_profile_is_still_valid_and_normalizes(self) -> None:
        self.assertIn("usb-host-session", audio_profile.VALID_PROFILES)
        self.assertEqual(
            audio_profile.normalize_profile("usb-host-session"), "usb-host-session"
        )

    def test_the_modal_renders_the_menu_not_the_vocabulary(self) -> None:
        """A stale PROFILE_OPTIONS reference in the modal would silently undo this."""
        src = (
            Path(__file__).resolve().parent.parent
            / "patch_browser" / "touch_browser_audio_profile_modal.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("PROFILE_OPTIONS", src)
        self.assertIn("menu_profile_options", src)

if __name__ == "__main__":
    unittest.main()
