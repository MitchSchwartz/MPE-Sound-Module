"""Unit tests for scripts/detect-audio-device.sh (mock Surge device list)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.hermetic_env import hermetic_env_with_profile

REPO_ROOT = Path(__file__).resolve().parents[1]
DETECT_SCRIPT = REPO_ROOT / "scripts" / "detect-audio-device.sh"

MOCK_GADGET_LIST = "\n".join(
    [
        "Output Audio Device [0.13] : Direct hardware device on ALSA.UAC2_Gadget",
        "Output Audio Device [0.14] : Direct sample mixing device on ALSA.UAC2_Gadget",
        "Output Audio Device [0.4] : Front output on Sound Blaster Play! 3",
    ]
)


def _run_detect(
    device_list: str,
    profile: str = "usb-host",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
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
        env["HOME"] = tmp
        env["MPE_UAC2_HOST_STREAMING_FLAG"] = str(Path(tmp) / "host-streaming")
        env.update(hermetic_env_with_profile(Path(tmp), profile))
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(detect), str(fake_surge)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


class DetectAudioDeviceTests(unittest.TestCase):
    def test_usb_host_idle_selects_sound_blaster(self) -> None:
        result = _run_detect(MOCK_GADGET_LIST, profile="usb-host")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.4", result.stdout)
        self.assertIn("TIER=1", result.stdout)
        self.assertIn("idle", result.stderr)

    def test_usb_host_streaming_selects_gadget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            flag = Path(tmp) / "host-streaming"
            flag.write_text("", encoding="utf-8")
            result = _run_detect(
                MOCK_GADGET_LIST,
                profile="usb-host",
                extra_env={"MPE_UAC2_HOST_STREAMING_FLAG": str(flag)},
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.13", result.stdout)
        self.assertIn("TIER=0", result.stdout)

    def test_usb_host_idle_no_sound_blaster_uses_pi_headphone(self) -> None:
        device_list = "\n".join(
            [
                "Output Audio Device [0.9] : Direct hardware device on ALSA.UAC2_Gadget",
                "Output Audio Device [0.1] : ALSA.bcm2835 Headphones, bcm2835 Headphones",
            ]
        )
        result = _run_detect(device_list, profile="usb-host")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.1", result.stdout)
        self.assertIn("TIER=3", result.stdout)
        self.assertIn("idle sink", result.stderr)

    def test_standalone_skips_gadget_tier0(self) -> None:
        result = _run_detect(MOCK_GADGET_LIST, profile="standalone")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.4", result.stdout)
        self.assertIn("TIER=1", result.stdout)

    def test_usb_host_session_always_sound_blaster(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            flag = Path(tmp) / "host-streaming"
            flag.write_text("", encoding="utf-8")
            result = _run_detect(
                MOCK_GADGET_LIST,
                profile="usb-host-session",
                extra_env={"MPE_UAC2_HOST_STREAMING_FLAG": str(flag)},
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.4", result.stdout)
        self.assertIn("TIER=1", result.stdout)
        self.assertIn("usb-host-session", result.stderr)


if __name__ == "__main__":
    unittest.main()


SCARLETT = "Output Audio Device [0.2] : ALSA.Scarlett 4i4 USB, USB Audio"
GADGET = "Output Audio Device [0.13] : Direct hardware device on ALSA.UAC2_Gadget"
HDMI = "Output Audio Device [0.12] : ALSA.vc4-hdmi-0, MAI PCM i2s-hifi-0"


class UsbHostIdleSinkTests(unittest.TestCase):
    """usb-host needs a LOCAL sink before it can ever reach the host.

    Reported 2026-08-28: switching to USB direct killed all audio. usb-host
    skipped tier 2 entirely, so a Focusrite Scarlett — which resolves nowhere
    else, tier 1 being a Sound Blaster product-name match — left the profile
    with no idle sink. Detection fell to tier 3, which matched vc4-hdmi, and
    jackd was then handed a card it could not play out of. Surge never came up,
    and because the host-route watcher starts from surge-xt-cli.service
    ExecStartPost, the USB route could never arm either.
    """

    def test_a_non_reference_usb_dac_is_the_idle_sink(self) -> None:
        result = _run_detect("\n".join([GADGET, HDMI, SCARLETT]), profile="usb-host")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DEVICE_ID=0.2", result.stdout)
        self.assertIn("TIER=2", result.stdout)
        self.assertIn("idle sink", result.stderr)

    def test_the_idle_sink_is_never_the_gadget(self) -> None:
        """Why tier 2 was skipped in the first place.

        Its match is a bare "usb", which selects the UAC2 gadget as readily as
        a DAC. Binding the gadget while the host is not capturing is the stall
        the profile exists to avoid, so the tier must run AND exclude it.
        """
        result = _run_detect("\n".join([GADGET, SCARLETT]), profile="usb-host")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("0.13", result.stdout)
        self.assertIn("DEVICE_ID=0.2", result.stdout)

    def test_hdmi_is_not_a_headphone_jack(self) -> None:
        """Tier 3's exclusion was `grep -v "HDMI"` — case-sensitive — while
        JUCE reports "ALSA.vc4-hdmi-0" in lower case."""
        result = _run_detect("\n".join([GADGET, HDMI]), profile="usb-host")
        # HDMI may still be taken as the honest last resort — it can actually
        # play, and a silent-but-working sink beats no instrument. What it must
        # never be is tier 3, a headphone jack this board does not have.
        self.assertNotIn("TIER=3", result.stdout)
        self.assertIn("TIER=4", result.stdout)

    def test_standalone_still_prefers_the_reference_dac(self) -> None:
        """Tier 1 must keep winning where the Sound Blaster is present."""
        blaster = "Output Audio Device [0.4] : Front output on Sound Blaster Play! 3"
        result = _run_detect("\n".join([SCARLETT, blaster]), profile="standalone")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("TIER=1", result.stdout)
        self.assertIn("DEVICE_ID=0.4", result.stdout)
