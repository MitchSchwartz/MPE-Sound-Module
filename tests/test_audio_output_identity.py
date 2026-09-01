"""Output device identity: what names a device, and what must never name one.

Spec: Documents/specs/audio-output-selection-spec.md section 2.

The assumption these tests exist to kill is that an ALSA card id or index
identifies anything. Measured on the appliance 2026-09-01: the card ids are
`USB` (Scarlett 4i4), `A` (Apple dongle) and `KA1` (FiiO KA1). `hw:0` was the
Apple dongle at 12:00 and the Scarlett at 15:02, same boot.

Fixtures mirror the appliance's real sysfs layout, including the two details
that were measured rather than assumed: /sys/class/sound/cardN/device points at
the USB *interface*, so identity lives in its PARENT; and the FiiO KA1 reports
the literal serial "0".
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib"


class OutputFixture:
    """A fake /proc/asound + /sys/class/sound tree."""

    def __init__(self, root: Path):
        self.root = root
        self.asound = root / "proc-asound"
        self.sysfs = root / "sys-class-sound"
        self.devices = root / "usb-devices"
        for d in (self.asound, self.sysfs, self.devices):
            d.mkdir(parents=True, exist_ok=True)
        self.lines: list[str] = []

    def add_card(self, index, card_id, *, description="USB-Audio - thing",
                 playback=True, capture=False, usb=None):
        self.lines.append(f" {index} [{card_id:<14}]: {description}")
        cdir = self.asound / f"card{index}"
        cdir.mkdir(parents=True, exist_ok=True)
        if playback:
            (cdir / "pcm0p").mkdir(exist_ok=True)
        if capture:
            (cdir / "pcm0c").mkdir(exist_ok=True)
        if usb is not None:
            # Real layout: device -> .../3-1.1.3:1.0, identity on the parent.
            devdir = self.devices / usb["bus_id"]
            iface = devdir / f"{usb['bus_id']}:1.0"
            iface.mkdir(parents=True, exist_ok=True)
            for key in ("idVendor", "idProduct", "serial", "speed", "product"):
                if usb.get(key) is not None:
                    (devdir / key).write_text(str(usb[key]), encoding="utf-8")
            (self.sysfs / f"card{index}").mkdir(parents=True, exist_ok=True)
            (self.sysfs / f"card{index}" / "device").symlink_to(iface)
        else:
            (self.sysfs / f"card{index}").mkdir(parents=True, exist_ok=True)

    def write(self):
        (self.asound / "cards").write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def sh(self, script: str) -> subprocess.CompletedProcess:
        preamble = (
            f'source "{LIB}/audio-engine.sh"\n'
            f'source "{LIB}/audio-outputs.sh"\n'
            f'export MPE_ASOUND_CARDS="{self.asound}/cards"\n'
            f'export MPE_ASOUND_ROOT="{self.asound}"\n'
            f'export MPE_SYSFS_SOUND="{self.sysfs}"\n'
        )
        return subprocess.run(["bash", "-c", preamble + script],
                              capture_output=True, text=True, timeout=30)


# The appliance as measured 2026-09-01, 18:12 EDT.
def appliance(root: Path) -> OutputFixture:
    f = OutputFixture(root)
    f.add_card(0, "KA1", description="USB-Audio - FiiO KA1", capture=True,
               usb={"bus_id": "3-1.1.3", "idVendor": "2972", "idProduct": "0051",
                    "serial": "0", "speed": "12", "product": "FiiO KA1"})
    f.add_card(1, "vc4hdmi0", description="vc4-hdmi - vc4-hdmi-0")
    f.add_card(3, "UAC2Gadget", description="UAC2_Gadget - UAC2_Gadget")
    # LUMI: enumerates as USB-Audio, has NO playback PCM at all.
    f.add_card(4, "BLOCK", description="USB-Audio - LUMI Keys BLOCK", playback=False,
               usb={"bus_id": "3-1.3", "idVendor": "2af4", "idProduct": "0e00",
                    "speed": "12", "product": "LUMI Keys BLOCK"})
    f.add_card(8, "Dummy", description="Dummy - Dummy", capture=True)
    f.write()
    return f


class SelectableOutputsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.f = appliance(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_the_real_dac_is_selectable(self):
        r = self.f.sh("mpe_output_records")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l for l in r.stdout.strip().splitlines() if l]
        self.assertEqual(len(rows), 1, f"expected only the KA1, got: {rows}")
        self.assertEqual(rows[0], "0|KA1|usb:2972:0051|12|FiiO KA1")

    def test_a_control_surface_with_no_playback_pcm_is_excluded(self):
        """The LUMI and the APC mini both enumerate as USB-Audio and can kill
        jackd. A name blocklist cannot see this; the pcm nodes can."""
        r = self.f.sh("mpe_output_records")
        self.assertNotIn("BLOCK", r.stdout)
        self.assertNotIn("2af4", r.stdout)

    def test_virtual_cards_are_excluded_by_the_shared_predicate(self):
        r = self.f.sh("mpe_output_records")
        for virtual in ("Dummy", "UAC2Gadget", "vc4hdmi0"):
            self.assertNotIn(virtual, r.stdout, f"{virtual} is selectable")

    def test_identity_comes_from_the_parent_of_the_interface(self):
        """cardN/device is the USB INTERFACE; idVendor lives on its parent."""
        r = self.f.sh('mpe_output_usb_dir 0 && echo && mpe_output_attr "$(mpe_output_usb_dir 0)" product')
        self.assertIn("FiiO KA1", r.stdout, r.stderr)


class SerialTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.f = appliance(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_zero_serial_does_not_become_part_of_the_key(self):
        """The KA1 reports the literal "0". Keying on it would promise a
        per-unit match the device cannot deliver, and two KA1s would both claim
        the same "unique" key with the ambiguity invisible."""
        r = self.f.sh("mpe_output_records")
        self.assertIn("usb:2972:0051|", r.stdout)
        self.assertNotIn("usb:2972:0051:0", r.stdout)

    def test_a_real_serial_is_kept(self):
        r = self.f.sh('mpe_output_key 1235 8212 F4N2X0Z')
        self.assertEqual(r.stdout.strip(), "usb:1235:8212:F4N2X0Z")

    def test_all_zero_serials_are_rejected(self):
        for junk in ("", "0", "0000", "00000000"):
            r = self.f.sh(f'mpe_output_serial_is_meaningful "{junk}" && echo yes || echo no')
            self.assertEqual(r.stdout.strip(), "no", f"{junk!r} treated as a serial")

    def test_a_serial_containing_a_zero_is_still_meaningful(self):
        """Negative control: the rule is all-zeros, not contains-a-zero."""
        r = self.f.sh('mpe_output_serial_is_meaningful "A0B" && echo yes || echo no')
        self.assertEqual(r.stdout.strip(), "yes")


class FindTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.f = appliance(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_present_device_is_found(self):
        r = self.f.sh("mpe_output_find usb:2972:0051")
        self.assertEqual(r.returncode, 0)
        self.assertIn("KA1", r.stdout)

    def test_absent_device_returns_non_zero(self):
        """Absence MUST be distinguishable from success -- the caller's entire
        job is to branch here and fall through to Automatic, by name."""
        r = self.f.sh("mpe_output_find usb:1235:8212")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_a_card_index_is_never_an_identity(self):
        """hw:0 was the Apple dongle at 12:00 and the Scarlett at 15:02."""
        for bad in ("0", "hw:0", "KA1"):
            r = self.f.sh(f"mpe_output_find '{bad}'")
            self.assertNotEqual(r.returncode, 0, f"{bad!r} resolved as an identity")


class AmbiguityTests(unittest.TestCase):
    """Two of the same model is a case to SURFACE, never to guess at."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        f = OutputFixture(Path(self._tmp.name))
        f.add_card(0, "KA1", description="USB-Audio - FiiO KA1",
                   usb={"bus_id": "3-1.1.3", "idVendor": "2972", "idProduct": "0051",
                        "serial": "0", "speed": "12", "product": "FiiO KA1"})
        f.add_card(1, "KA1_1", description="USB-Audio - FiiO KA1",
                   usb={"bus_id": "3-1.2", "idVendor": "2972", "idProduct": "0051",
                        "serial": "0", "speed": "12", "product": "FiiO KA1"})
        f.write()
        self.f = f

    def tearDown(self):
        self._tmp.cleanup()

    def test_both_matches_are_emitted_never_head_minus_one(self):
        r = self.f.sh("mpe_output_find usb:2972:0051")
        rows = [l for l in r.stdout.strip().splitlines() if l]
        self.assertEqual(len(rows), 2, f"ambiguity was silently resolved: {rows}")


class SelectionParsingTests(unittest.TestCase):
    def sh(self, value, script):
        return subprocess.run(
            ["bash", "-c",
             f'source "{LIB}/audio-engine.sh"; source "{LIB}/audio-outputs.sh"; '
             f'export MPE_AUDIO_OUTPUT={value!r}; {script}'],
            capture_output=True, text=True, timeout=30)

    def test_unset_and_auto_are_the_same(self):
        for value in ("", "auto", "AUTO", "  auto  "):
            self.assertEqual(self.sh(value, "mpe_output_selection").stdout.strip(), "auto")

    def test_silent_is_its_own_value(self):
        self.assertEqual(self.sh("silent", "mpe_output_selection").stdout.strip(), "silent")

    def test_only_a_device_key_counts_as_explicit(self):
        for value, explicit in (("auto", False), ("silent", False),
                                ("usb:2972:0051", True)):
            r = self.sh(value, "mpe_output_selection_is_explicit && echo yes || echo no")
            self.assertEqual(r.stdout.strip(), "yes" if explicit else "no", value)


class SpeedLabelTests(unittest.TestCase):
    """Speed is on the menu row because it predicts the smallest usable period
    and is otherwise invisible. Measured 2026-09-01: the Apple dongle and the
    FiiO KA1 both enumerate at 12 and neither starts a driver at 64; the
    Scarlett enumerates at 480 and runs 64 and 32 clean."""

    def sh(self, arg):
        return subprocess.run(
            ["bash", "-c",
             f'source "{LIB}/audio-engine.sh"; source "{LIB}/audio-outputs.sh"; '
             f'mpe_output_speed_label {arg!r}'],
            capture_output=True, text=True, timeout=30).stdout.strip()

    def test_labels(self):
        self.assertEqual(self.sh("12"), "full speed")
        self.assertEqual(self.sh("480"), "high speed")
        self.assertEqual(self.sh(""), "unknown speed")


if __name__ == "__main__":
    unittest.main()
