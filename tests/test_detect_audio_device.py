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


MOCK_PI5_USB_HOST_NO_DAC = "\n".join(
    [
        # What a Pi 5 in usb-host actually offers with nothing plugged in:
        # the gadget, two disconnected HDMI ports, ALSA's virtual default, and
        # the loopback idle sink. No headphone jack — that is a Pi 4 part.
        "Output Audio Device [0.13] : Direct hardware device on ALSA.UAC2_Gadget",
        "Output Audio Device [0.0] : ALSA.Default Audio Device (1)",
        "Output Audio Device [0.1] : Direct hardware device on ALSA.vc4-hdmi-0",
        "Output Audio Device [0.2] : Direct hardware device on ALSA.vc4-hdmi-1",
        "Output Audio Device [0.7] : Direct hardware device on ALSA.Loopback",
    ]
)


class Pi5IdleSinkTests(unittest.TestCase):
    """usb-host on a Pi 5 with no external DAC must still resolve a sink.

    The `usb-host` profile refuses to bind the UAC2 gadget until the host is
    actually capturing, and that refusal is not a policy — MEASURED on the
    appliance 2026-08-30 with the host attached but idle:

        aplay -D <gadget>   -> write error: Input/output error, after 1s
        aplay -D <Loopback> -> 3 s of audio took 3 s, nothing reading it

    A UAC2 gadget has no clock of its own; the host enables the streaming
    interface and isochronous transfers only happen while it is active. So the
    graph runs on a free-running local device and bridges into the gadget when
    the host appears.

    `docs/USB-AUDIO-HOST.md` gives the Pi 4 answer for that local device — "No
    external DAC: idle sink is Pi headphone". The Pi 5 has no headphone jack
    and its HDMI ports read `disconnected` with no display, so there was no
    idle sink at all: jackd failed, Surge failed, the appliance went silent.
    """

    def test_the_loopback_is_the_idle_sink_when_nothing_else_exists(self) -> None:
        result = _run_detect(MOCK_PI5_USB_HOST_NO_DAC, profile="usb-host")
        self.assertIn("TIER=3", result.stdout, result.stderr)
        self.assertIn("DEVICE_ID=0.7", result.stdout, result.stderr)

    def test_it_never_outranks_a_real_interface(self) -> None:
        """A plugged-in DAC must win. An idle sink that captured the graph
        while real hardware was attached would be silent output on purpose."""
        with_dac = MOCK_PI5_USB_HOST_NO_DAC + (
            "\nOutput Audio Device [0.4] : Front output on Sound Blaster Play! 3"
        )
        result = _run_detect(with_dac, profile="usb-host")
        self.assertIn("DEVICE_ID=0.4", result.stdout, result.stderr)
        self.assertNotIn("TIER=3", result.stdout)

    def test_the_gadget_still_wins_once_the_host_captures(self) -> None:
        """Tier 0 must keep its precedence — the idle sink is only for idle."""
        with tempfile.TemporaryDirectory() as tmp:
            flag = Path(tmp) / "streaming"
            flag.write_text("48000", encoding="utf-8")
            result = _run_detect(
                MOCK_PI5_USB_HOST_NO_DAC,
                profile="usb-host",
                extra_env={"MPE_UAC2_HOST_STREAMING_FLAG": str(flag)},
            )
        self.assertIn("TIER=0", result.stdout, result.stderr)

    def test_no_sink_at_all_fails_loudly(self) -> None:
        """Tier 4's own comment promises this and did not deliver it.

        On 2026-08-30 it returned `ALSA.Default Audio Device (1)` — not a card.
        `jackd-prestart.sh` could not map it to anything in /proc/asound/cards
        and failed one layer later with "no ALSA card matches tier '4'", an
        error that sent a diagnosis hunting for a missing sound interface
        instead of reporting that no sink existed.
        """
        nothing_real = "\n".join(
            [
                "Output Audio Device [0.13] : Direct hardware device on ALSA.UAC2_Gadget",
                "Output Audio Device [0.0] : ALSA.Default Audio Device (1)",
            ]
        )
        result = _run_detect(nothing_real, profile="usb-host")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("DEVICE_ID=0.0", result.stdout,
                         "returned ALSA's virtual default as if it were a card")
