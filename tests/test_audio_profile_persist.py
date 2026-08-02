"""Tests for persisted MPE_AUDIO_PROFILE across configure and boot."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class AudioProfilePersistTests(unittest.TestCase):
    def test_configure_force_preserves_usb_host_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_file = tmp_path / "mpe.env"
            env_file.write_text(
                "MPE_PI_USER=mitch\nMPE_AUDIO_PROFILE=usb-host\nMPE_SURGE_BUFFER_SIZE=512\n",
                encoding="utf-8",
            )
            script = REPO_ROOT / "scripts" / "lib" / "mpe-services.sh"
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"""
                    source {script}
                    mpe_read_appliance_env_var() {{
                      grep -E "^${{1}}=" "{env_file}" | tail -1 | cut -d= -f2-
                    }}
                    profile="$(mpe_read_appliance_env_var MPE_AUDIO_PROFILE)"
                    buffer="$(mpe_read_appliance_env_var MPE_SURGE_BUFFER_SIZE)"
                    echo "profile=$profile buffer=$buffer"
                    """,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("profile=usb-host", result.stdout)
            self.assertIn("buffer=512", result.stdout)

    def test_sync_on_boot_script_exists(self) -> None:
        sync = REPO_ROOT / "scripts" / "sync-audio-profile-on-boot.sh"
        self.assertTrue(sync.is_file())
        self.assertTrue(os.access(sync, os.X_OK))

    def test_boot_sync_unit_installed_in_config(self) -> None:
        unit = REPO_ROOT / "config" / "mpe-audio-profile-sync.service"
        self.assertTrue(unit.is_file())
        text = unit.read_text(encoding="utf-8")
        self.assertIn("sync-audio-profile-on-boot.sh", text)
        self.assertIn("Before=surge-xt-cli.service", text)

    def test_mpe_services_enables_profile_sync(self) -> None:
        content = (REPO_ROOT / "scripts" / "lib" / "mpe-services.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("mpe_enable_audio_profile_sync", content)
        self.assertIn("mpe-audio-profile-sync.service", content)
        self.assertIn("mpe_source_appliance_env", content)


if __name__ == "__main__":
    unittest.main()
