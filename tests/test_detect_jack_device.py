"""detect-jack-device.sh — translating a tier into an ALSA card jackd can bind.

The failure this file exists for: on 2026-08-28 jackd was handed `hw:0`, the
APC mini mk2. It is a control surface. It enumerates as USB-Audio, it appears
in /proc/asound/cards like any interface, and it has no playback PCM at all —
so jackd reported "ALSA: Cannot open PCM device alsa_pcm for playback", died,
took Surge with it, and the appliance went silent. The last-resort selector
filtered on card NAMES, which cannot see the difference.
"""

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

# The appliance's own cards, 2026-08-28. Card 0 is the control surface.
CARDS = """\
 0 [mk2            ]: USB-Audio - APC mini mk2
                      AKAI professional APC mini mk2 at usb-xhci-hcd.1-1.2, full speed
 1 [USB            ]: USB-Audio - Scarlett 4i4 USB
                      Focusrite Scarlett 4i4 USB at usb-xhci-hcd.1-1.4, high speed
 2 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 4 [UAC2Gadget     ]: UAC2_Gadget - UAC2_Gadget
                      UAC2_Gadget 0
"""


def _run(device_list: str, profile: str, playback_cards: set[int]):
    """Resolve a jack device against a fake card tree.

    `playback_cards` are the card indexes given a `pcmNp` node — the only
    evidence that a card can actually play.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        scripts_dir = tmp_path / "scripts"
        shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
        for name in ("detect-jack-device.sh", "detect-audio-device.sh"):
            shutil.copy2(REPO_ROOT / "scripts" / name, scripts_dir / name)

        cards_file = tmp_path / "cards"
        cards_file.write_text(CARDS, encoding="utf-8")

        asound = tmp_path / "asound"
        for idx in (0, 1, 2, 4):
            card_dir = asound / f"card{idx}"
            card_dir.mkdir(parents=True)
            (card_dir / "pcm0c").mkdir()
            if idx in playback_cards:
                (card_dir / "pcm0p").mkdir()

        fake_surge = tmp_path / "fake-surge-xt-cli"
        body = '#!/bin/bash\nif [ "$1" = "--list-devices" ]; then\n'
        for line in device_list.split("\n"):
            body += f"  echo {line!r}\n"
        body += "fi\n"
        fake_surge.write_text(body, encoding="utf-8")
        fake_surge.chmod(fake_surge.stat().st_mode | stat.S_IXUSR)

        env = os.environ.copy()
        env["HOME"] = tmp
        env["MPE_ASOUND_CARDS"] = str(cards_file)
        env["MPE_ASOUND_ROOT"] = str(asound)
        env["MPE_UAC2_HOST_STREAMING_FLAG"] = str(tmp_path / "host-streaming")
        env.update(hermetic_env_with_profile(tmp_path, profile))
        return subprocess.run(
            [str(scripts_dir / "detect-jack-device.sh"), str(fake_surge)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


class LastResortTests(unittest.TestCase):
    def test_a_card_with_no_playback_pcm_is_never_chosen(self) -> None:
        """The APC is card 0 and would win any "first physical card" race."""
        result = _run(
            "Output Audio Device [0.12] : ALSA.vc4-hdmi-0, MAI PCM i2s-hifi-0",
            profile="usb-host",
            playback_cards={1, 2, 4},
        )
        self.assertNotIn("hw:0", result.stdout, "that is the control surface")
        self.assertNotIn("JACK_CARD_ID=mk2", result.stdout)

    def test_the_scarlett_is_resolved_for_the_usb_host_idle_sink(self) -> None:
        result = _run(
            "Output Audio Device [0.2] : ALSA.Scarlett 4i4 USB, USB Audio",
            profile="usb-host",
            playback_cards={1, 2, 4},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("JACK_DEVICE=hw:1", result.stdout)
        self.assertIn("JACK_CARD_ID=USB", result.stdout)

    def test_no_playback_capable_card_at_all_fails_loudly(self) -> None:
        """Silence with an error beats jackd wedging on a device that cannot
        play — the unit fails, Restart=always retries, and the UI can say so."""
        result = _run(
            "Output Audio Device [0.12] : ALSA.vc4-hdmi-0, MAI PCM i2s-hifi-0",
            profile="usb-host",
            playback_cards=set(),
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
