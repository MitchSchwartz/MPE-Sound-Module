"""Device-level volume: element choice and mapping, against real Pi dumps.

Fixtures are verbatim `amixer -c N contents` from the appliance, 2026-09-13,
with the FiiO KA1, Sound Blaster Play! 3 and Scarlett 4i4 all connected. They
are not hand-written: a hand-written fixture is the parser's own assumptions
fed back to it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from patch_browser import device_volume as dv

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "amixer"


def _contents(card_id: str) -> str:
    return (FIXTURES / f"{card_id}.contents.txt").read_text(encoding="utf-8")


class TestElementChoice(unittest.TestCase):
    def test_ka1_is_device_mode_on_pcm(self):
        el = dv.choose_element(dv.parse_contents(_contents("KA1")))
        self.assertIsNotNone(el)
        self.assertEqual(el.name, "PCM Playback Volume")
        self.assertEqual((el.raw_min, el.raw_max, el.db_min, el.db_max), (0, 127, -63.5, 0.0))

    def test_sound_blaster_is_speaker_not_mic_monitor(self):
        el = dv.choose_element(dv.parse_contents(_contents("S3")))
        self.assertIsNotNone(el)
        self.assertEqual(el.name, "Speaker Playback Volume")

    def test_scarlett_is_ambiguous_so_trim(self):
        elements = dv.parse_contents(_contents("USB"))
        self.assertGreater(len(elements), 1, "the fixture must show the ambiguity")
        self.assertIsNone(dv.choose_element(elements))

    def test_no_elements_is_trim(self):
        self.assertIsNone(dv.choose_element(dv.parse_contents("")))


class TestMapping(unittest.TestCase):
    """Pinned to what alsamixer / `amixer -M` printed on the Pi, not to the formula."""

    def setUp(self):
        self.ka1 = dv.choose_element(dv.parse_contents(_contents("KA1")))
        self.s3 = dv.choose_element(dv.parse_contents(_contents("S3")))

    def test_measured_mapped_percentages(self):
        self.assertEqual(round(self.ka1.position_from_db(-4.0) * 100), 84)
        self.assertEqual(round(self.s3.position_from_db(-12.0) * 100), 55)
        # The reported symptom: KA1 restored to raw 87 by ALSA = 41%.
        self.assertEqual(self.ka1.raw_to_db(87), -20.0)
        self.assertEqual(round(self.ka1.position_from_db(-20.0) * 100), 41)

    def test_raw_db_matches_driver(self):
        self.assertEqual(self.ka1.raw_to_db(119), -4.0)
        self.assertEqual(self.s3.db_to_raw(-12.0), 64)

    def test_position_round_trip(self):
        for el in (self.ka1, self.s3):
            for db in (-40.0, -12.0, -3.0, 0.0):
                self.assertAlmostEqual(el.db_from_position(el.position_from_db(db)), db, places=6)

    def test_bottom_and_top(self):
        self.assertEqual(self.ka1.db_to_raw(self.ka1.db_from_position(0.0)), 0)
        self.assertEqual(self.ka1.db_to_raw(self.ka1.db_from_position(1.0)), 127)


class FakeAmixer:
    def __init__(self, contents: str, fail_cset: bool = False):
        self.contents = contents
        self.fail_cset = fail_cset
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[2] == "contents":
            return self.contents
        if args[2] == "cset":
            return None if self.fail_cset else "ok"
        return None

    def csets(self):
        return [c for c in self.calls if c[2] == "cset"]


class TestBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.asound = root / "asound"
        (self.asound / "card4").mkdir(parents=True)
        (self.asound / "card4" / "usbid").write_text("2972:0051\n")
        (self.asound / "card6").mkdir(parents=True)
        (self.asound / "card6" / "usbid").write_text("1235:8212\n")
        self.state = root / "levels.json"
        self.env = root / "mpe.env"
        self.env.write_text("MPE_DAC_VOLUME_DB=-12\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _dv(self, runner):
        return dv.DeviceVolume(runner=runner, state_file=self.state,
                               asound_root=self.asound, env_path=self.env)

    def test_new_device_gets_default_minus_12(self):
        amixer = FakeAmixer(_contents("KA1"))
        vol = self._dv(amixer)
        self.assertTrue(vol.rebind({"started": "1", "device": "hw:4"}))
        self.assertEqual(vol.mode, dv.DEVICE_MODE)
        self.assertEqual(amixer.csets(), [["-c", "4", "cset", "numid=3", "103"]])
        self.assertEqual(vol.db(), -12.0)

    def test_known_device_gets_its_saved_level(self):
        self.state.write_text(json.dumps({"usb:2972:0051": -3.0}))
        amixer = FakeAmixer(_contents("KA1"))
        vol = self._dv(amixer)
        vol.rebind({"started": "1", "device": "hw:4"})
        self.assertEqual(amixer.csets()[-1][-1], "121")

    def test_rebind_is_a_noop_until_the_graph_restarts(self):
        amixer = FakeAmixer(_contents("KA1"))
        vol = self._dv(amixer)
        vol.rebind({"started": "1", "device": "hw:4"})
        n = len(amixer.calls)
        self.assertFalse(vol.rebind({"started": "1", "device": "hw:4"}))
        self.assertEqual(len(amixer.calls), n, "no fork when nothing changed")
        self.assertTrue(vol.rebind({"started": "2", "device": "hw:4"}))

    def test_scarlett_binds_to_trim_and_writes_nothing(self):
        amixer = FakeAmixer(_contents("USB"))
        vol = self._dv(amixer)
        vol.rebind({"started": "1", "device": "hw:6"})
        self.assertEqual(vol.mode, dv.TRIM_MODE)
        self.assertEqual(amixer.csets(), [])
        self.assertIsNone(vol.position())

    def test_unknown_device_string_is_trim(self):
        vol = self._dv(FakeAmixer(_contents("KA1")))
        vol.rebind({"started": "1", "device": "dummy"})
        self.assertEqual(vol.mode, dv.TRIM_MODE)

    def test_set_position_persists_per_model_and_writes(self):
        amixer = FakeAmixer(_contents("KA1"))
        vol = self._dv(amixer)
        vol.rebind({"started": "1", "device": "hw:4"})
        vol.set_position(1.0)
        vol.flush()
        self.assertEqual(amixer.csets()[-1][-1], "127")
        self.assertEqual(json.loads(self.state.read_text()), {"usb:2972:0051": 0.0})

    def test_failed_write_reports_device_value_not_the_request(self):
        amixer = FakeAmixer(_contents("KA1"), fail_cset=True)
        vol = self._dv(amixer)
        vol.rebind({"started": "1", "device": "hw:4"})
        # Fixture holds raw 119 = -4 dB; the -12 dB write failed.
        self.assertEqual(vol.db(), -4.0)


class TestSingleDecisionSite(unittest.TestCase):
    """The DAC level had a second, name-matching decision site once — it is why
    the KA1 was never set. Refuse a new one."""

    def test_no_other_code_writes_a_playback_volume(self):
        repo = Path(__file__).resolve().parents[1]
        owner = repo / "patch_browser" / "device_volume.py"
        offenders = []
        for base in ("scripts", "patch_browser", "config"):
            for path in (repo / base).rglob("*"):
                if not path.is_file() or path == owner or path.suffix not in {".py", ".sh", ".rules", ""}:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for needle in ("mpe_apply_dac_volume", "set-dac-volume", "Playback Volume",
                               "sset Speaker", "sset PCM "):
                    if needle in text:
                        offenders.append(f"{path.relative_to(repo)}: {needle}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
