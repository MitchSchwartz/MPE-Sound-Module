"""paths.sh respects MPE_ENV_FILE for hermetic subprocess tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.hermetic_env import hermetic_env_skip_system, hermetic_env_with_profile

REPO_ROOT = Path(__file__).resolve().parents[1]
PATHS_SH = REPO_ROOT / "scripts" / "lib" / "paths.sh"


class MpeEnvFileTests(unittest.TestCase):
    def _profile_from_paths(self, env: dict[str, str]) -> str:
        body = f"""
source {PATHS_SH}
printf '%s' "${{MPE_AUDIO_PROFILE:-}}"
"""
        result = subprocess.run(
            ["bash", "-c", body],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_empty_mpe_env_file_uses_process_env(self) -> None:
        env = os.environ.copy()
        env.update(hermetic_env_skip_system())
        env["MPE_AUDIO_PROFILE"] = "usb-host"
        self.assertEqual(self._profile_from_paths(env), "usb-host")

    def test_mpe_env_file_overrides_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["MPE_AUDIO_PROFILE"] = "standalone"
            env.update(hermetic_env_with_profile(Path(tmp), "usb-host-session"))
            self.assertEqual(self._profile_from_paths(env), "usb-host-session")

    @mock.patch.dict(
        os.environ,
        {"MPE_ENV_FILE": "", "MPE_AUDIO_PROFILE": "usb-host"},
        clear=False,
    )
    def test_profile_env_path_none_when_hermetic(self) -> None:
        from patch_browser import audio_profile

        self.assertIsNone(audio_profile.profile_env_path())
        self.assertEqual(audio_profile.current_profile(), "usb-host")


if __name__ == "__main__":
    unittest.main()
