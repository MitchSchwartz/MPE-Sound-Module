"""Unit tests for scripts/detect-audio-device.sh (mock Surge device list)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DETECT_SCRIPT = REPO_ROOT / "scripts" / "detect-audio-device.sh"

MOCK_GADGET_LIST = "\n".join(
    [
        "Output Audio Device [0.13] : Direct hardware device on ALSA.UAC2_Gadget",
        "Output Audio Device [0.14] : Direct sample mixing device on ALSA.UAC2_Gadget",
        "Output Audio Device [0.4] : Front output on Sound Blaster Play! 3",
    ]
)


def _run_detect(device_list: str, profile: str = "usb-host") -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        scripts_dir = Path(tmp) / "scripts"
        shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
        shutil.copy2(DETECT_SCRIPT, scripts_dir / "detect-audio-device.sh")
        detect = scripts_dir / "detect-audio-device.sh"

        fake_surge = Path(tmp) / "fake-surge-xt-cli"
        lines = device_list.split("\n")
        fake_body = "#!/bin/bash\nif [ \"$1\" = \"--list-devices\" ]; then\n"
        for line in lines:
            fake_body += f"  echo {line!r}\n"
        fake_body += "fi\n"
        fake_surge.write_text(fake_body, encoding="utf-8")
        fake_surge.chmod(fake_surge.stat().st_mode | stat.S_IXUSR)

        env = os.environ.copy()
        env["MPE_AUDIO_PROFILE"] = profile
        env["HOME"] = tmp
        return subprocess.run(
            [str(detect), str(fake_surge)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


class DetectAudioDeviceTests(unittest.TestCase):
    def test_usb_host_selects_gadget_direct_hardware_tier0(self) -> None:
        result = _run_detect(MOCK_GADGET_LIST, profile="usb-host")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.13", result.stdout)
        self.assertIn("TIER=0", result.stdout)
        self.assertIn("UAC2_Gadget", result.stdout)

    def test_usb_host_does_not_fallback_to_sound_blaster(self) -> None:
        result = _run_detect(MOCK_GADGET_LIST, profile="usb-host")
        self.assertNotIn("TIER=1", result.stdout)
        self.assertNotIn("DEVICE_ID=0.4", result.stdout)

    def test_standalone_skips_gadget_tier0(self) -> None:
        result = _run_detect(MOCK_GADGET_LIST, profile="standalone")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.4", result.stdout)
        self.assertIn("TIER=1", result.stdout)

    def test_usb_host_matches_mpe_sound_module_name(self) -> None:
        device_list = (
            "Output Audio Device [2.0] : Direct hardware device on ALSA.MPE Sound Module"
        )
        result = _run_detect(device_list, profile="usb-host")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=2.0", result.stdout)
        self.assertIn("TIER=0", result.stdout)


if __name__ == "__main__":
    unittest.main()
