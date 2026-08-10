"""Tests for post-Surge UAC2 watchdog starter."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = REPO_ROOT / "scripts" / "start-uac2-watchdog-if-needed.sh"
SURGE_UNIT = REPO_ROOT / "config" / "surge-xt-cli.service"


class StartUac2WatchdogTests(unittest.TestCase):
    def test_script_exists_and_executable(self) -> None:
        self.assertTrue(START_SCRIPT.is_file())
        mode = START_SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)

    def test_surge_unit_starts_watchdog_after_surge(self) -> None:
        text = SURGE_UNIT.read_text(encoding="utf-8")
        self.assertIn("start-uac2-watchdog-if-needed.sh", text)
        self.assertIn("ExecStartPost=+", text)

    def test_skips_when_not_usb_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["bash", str(START_SCRIPT)],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "MPE_AUDIO_PROFILE": "standalone",
                },
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_starts_for_usb_host_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            marker = tmp_path / "watchdog-started"
            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                f"#!/bin/bash\n"
                f'if [ "$1" = "is-active" ]; then exit 1; fi\n'
                f'if [ "$1" = "start" ]; then touch {marker}; fi\n',
                encoding="utf-8",
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)
            result = subprocess.run(
                ["bash", str(START_SCRIPT)],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "MPE_AUDIO_PROFILE": "usb-host-session",
                    "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                },
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(marker.exists())

    def test_mpe_services_does_not_inline_start_watchdog(self) -> None:
        content = (REPO_ROOT / "scripts" / "lib" / "mpe-services.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("start_watchdog", content)
        self.assertNotIn("systemctl start uac2-stall-watchdog", content)


if __name__ == "__main__":
    unittest.main()
