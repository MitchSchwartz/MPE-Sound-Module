"""Explicit output selection: preference, never command.

Spec: Documents/specs/audio-output-selection-spec.md section 4 -- "This is the
section that matters. Get it wrong and the whole feature is a liability."

Mitch resolved the rule 2026-09-01: a stored selection is applied only when the
device is actually PRESENT. When it is not, the appliance falls through to
Automatic and says so by name. A rig that is silent at soundcheck is worse than
one that is on the wrong output and says so -- so the prohibition is on the
SILENCE, not on the substitution.

These run detect-jack-device.sh for real against a fake /proc and /sys.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Two real devices, measured on the appliance 2026-09-01.
KA1 = {"bus_id": "3-1.1.3", "idVendor": "2972", "idProduct": "0051",
       "serial": "0", "speed": "12", "product": "FiiO KA1"}
SCARLETT = {"bus_id": "3-1.4", "idVendor": "1235", "idProduct": "8212",
            "serial": "F4N2X0Z", "speed": "480", "product": "Scarlett 4i4 USB"}

KA1_KEY = "usb:2972:0051"
SCARLETT_KEY = "usb:1235:8212:F4N2X0Z"


def _run(devices, *, selection=None, label=None, profile="standalone",
         device_list="Output Audio Device [0.4] : Front output on FiiO KA1"):
    """devices: list of (card_index, card_id, usb_dict_or_None, has_playback)."""
    tmp = tempfile.mkdtemp()
    tmp_path = Path(tmp)
    asound = tmp_path / "asound"
    sysfs = tmp_path / "sys-sound"
    usbroot = tmp_path / "usb"
    for d in (asound, sysfs, usbroot):
        d.mkdir(parents=True, exist_ok=True)

    lines = []
    for idx, card_id, usb, playback in devices:
        desc = usb["product"] if usb else card_id
        lines.append(f" {idx} [{card_id:<14}]: USB-Audio - {desc}")
        cdir = asound / f"card{idx}"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "pcm0c").mkdir(exist_ok=True)
        if playback:
            (cdir / "pcm0p").mkdir(exist_ok=True)
        (sysfs / f"card{idx}").mkdir(parents=True, exist_ok=True)
        if usb:
            devdir = usbroot / usb["bus_id"]
            iface = devdir / f"{usb['bus_id']}:1.0"
            iface.mkdir(parents=True, exist_ok=True)
            for k in ("idVendor", "idProduct", "serial", "speed", "product"):
                if usb.get(k) is not None:
                    (devdir / k).write_text(str(usb[k]), encoding="utf-8")
            (sysfs / f"card{idx}" / "device").symlink_to(iface)
    (asound / "cards").write_text("\n".join(lines) + "\n", encoding="utf-8")

    fake_surge = tmp_path / "fake-surge-xt-cli"
    body = '#!/bin/bash\nif [ "$1" = "--list-devices" ]; then\n'
    for line in device_list.split("\n"):
        body += f"  echo {line!r}\n"
    body += "fi\n"
    fake_surge.write_text(body, encoding="utf-8")
    fake_surge.chmod(fake_surge.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["HOME"] = tmp
    env["MPE_ASOUND_CARDS"] = str(asound / "cards")
    env["MPE_ASOUND_ROOT"] = str(asound)
    env["MPE_SYSFS_SOUND"] = str(sysfs)
    env["MPE_ENV_FILE"] = ""
    env["MPE_AUDIO_PROFILE"] = profile
    env["MPE_UAC2_HOST_STREAMING_FLAG"] = str(tmp_path / "host-streaming")
    if selection is not None:
        env["MPE_AUDIO_OUTPUT"] = selection
    else:
        env.pop("MPE_AUDIO_OUTPUT", None)
    if label is not None:
        env["MPE_AUDIO_OUTPUT_LABEL"] = label
    return subprocess.run(
        [str(SCRIPTS / "detect-jack-device.sh"), str(fake_surge)],
        capture_output=True, text=True, env=env, check=False, timeout=60,
    )


BOTH = [(0, "KA1", KA1, True), (1, "USB", SCARLETT, True)]
ONLY_KA1 = [(0, "KA1", KA1, True)]


class ChosenDevicePresentTests(unittest.TestCase):
    def test_the_chosen_device_is_bound_not_the_first_one(self):
        """The KA1 is card 0 and wins every "first card" race. Selecting the
        Scarlett must beat it -- that is the entire point of the feature."""
        r = _run(BOTH, selection=SCARLETT_KEY)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("JACK_DEVICE=hw:1", r.stdout)
        self.assertIn("JACK_CARD_ID=USB", r.stdout)

    def test_selecting_the_other_one_binds_the_other_one(self):
        """Negative control: without this, a resolver that always returns card 0
        would pass the test above by accident."""
        r = _run(BOTH, selection=KA1_KEY)
        self.assertIn("JACK_DEVICE=hw:0", r.stdout)
        self.assertIn("JACK_CARD_ID=KA1", r.stdout)

    def test_the_reason_names_the_device_and_its_speed(self):
        """Speed is otherwise invisible and predicts the smallest usable period."""
        r = _run(BOTH, selection=SCARLETT_KEY)
        self.assertIn("Scarlett 4i4 USB", r.stderr)
        self.assertIn("high speed", r.stderr)

    def test_tier_detection_is_not_consulted_at_all(self):
        """An explicit choice is not a tier and must not be re-derived by one."""
        r = _run(BOTH, selection=SCARLETT_KEY)
        self.assertIn("TIER=selected", r.stdout)


class ChosenDeviceAbsentTests(unittest.TestCase):
    def test_it_falls_through_to_automatic_and_still_makes_sound(self):
        r = _run(ONLY_KA1, selection=SCARLETT_KEY)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("JACK_DEVICE=hw:0", r.stdout)

    def test_it_says_the_device_is_not_connected_by_name(self):
        """'Scarlett 4i4 not connected', not 'no audio output'."""
        r = _run(ONLY_KA1, selection=SCARLETT_KEY, label="Scarlett 4i4 USB")
        self.assertIn("NOT CONNECTED", r.stderr)
        self.assertIn("Scarlett 4i4 USB", r.stderr)

    def test_it_says_the_output_is_not_what_was_chosen(self):
        """Substituting is allowed. Substituting SILENTLY is not."""
        r = _run(ONLY_KA1, selection=SCARLETT_KEY)
        self.assertIn("not what you chose", r.stderr)

    def test_without_a_label_it_still_names_the_key(self):
        r = _run(ONLY_KA1, selection=SCARLETT_KEY)
        self.assertIn(SCARLETT_KEY, r.stderr)


class AmbiguousSelectionTests(unittest.TestCase):
    """Two devices matching one identity: surface it, do not pick one."""

    def _two_of_a_kind(self):
        second = dict(KA1, bus_id="3-1.2")
        return [(0, "KA1", KA1, True), (1, "KA1_1", second, True)]

    def test_it_refuses_to_guess(self):
        r = _run(self._two_of_a_kind(), selection=KA1_KEY)
        self.assertIn("Refusing to guess", r.stderr)
        self.assertIn("2 devices match", r.stderr)

    def test_it_lists_the_candidates(self):
        r = _run(self._two_of_a_kind(), selection=KA1_KEY)
        self.assertIn("candidate:", r.stderr)

    def test_it_still_produces_sound_rather_than_failing_closed(self):
        r = _run(self._two_of_a_kind(), selection=KA1_KEY)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("JACK_DEVICE=hw:", r.stdout)


class AutomaticAndSilentTests(unittest.TestCase):
    def test_automatic_is_the_default_and_uses_tier_detection(self):
        r = _run(BOTH)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("TIER=selected", r.stdout)
        self.assertNotIn("NOT CONNECTED", r.stderr)

    def test_the_literal_string_auto_is_the_same_as_unset(self):
        a = _run(BOTH, selection="auto")
        b = _run(BOTH)
        self.assertEqual(a.stdout, b.stdout)

    def test_silent_binds_the_idle_sink_on_purpose_and_says_so(self):
        devices = list(BOTH) + [(8, "Dummy", None, True)]
        r = _run(devices, selection="silent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("on purpose", r.stderr)
        self.assertIn("JACK_CARD_ID=Dummy", r.stdout)


class NeverIndexOrCardIdTests(unittest.TestCase):
    """hw:0 was the Apple dongle at 12:00 and the Scarlett at 15:02, same boot."""

    def test_a_card_index_selection_is_treated_as_absent_not_obeyed(self):
        r = _run(BOTH, selection="hw:1")
        self.assertIn("NOT CONNECTED", r.stderr)
        self.assertIn("JACK_DEVICE=hw:0", r.stdout)

    def test_a_card_id_selection_is_treated_as_absent_not_obeyed(self):
        r = _run(BOTH, selection="USB")
        self.assertIn("NOT CONNECTED", r.stderr)


if __name__ == "__main__":
    unittest.main()
