"""Device mode keeps 9 dB of headroom in the stage gain; trim mode is the fader.

Since a51a975 (2026-09-13) the Vol fader drives the DAC when the bound card
allows it. Normalization targets -18 LUFS with a -3 dBTP peak cap on test
notes, so live chords with pressure, and the FX host's added peaks, reached
0 dBFS on calibrated patches. Device mode holds the stage at
`DEVICE_MODE_SURGE_TRIM_DB`; the DAC has the analogue range to make it up.
Since 2026-10-19 the headroom is a term of `stage_db`, applied by mpe-fx-host,
not a Surge amp/volume (which read it as a cube law: -27 dB, not -9).
"""

from __future__ import annotations

import math
import re
import tempfile
import unittest
from pathlib import Path

from patch_browser import device_volume as dv
from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_normalization import (
    PatchNormalizationStore,
    volume_fader_trim_to_db,
)
from patch_browser.touch_browser_prefs import TouchBrowserPrefsMixin
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

REPO = Path(__file__).resolve().parents[1]


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value: float) -> None:
        self.messages.append((address, value))


class Stage:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def __call__(self, payload: bytes) -> bool:
        self.payloads.append(payload)
        return True

    def db(self) -> float:
        linear = float(self.payloads[-1].decode().split()[1])
        return -math.inf if linear == 0.0 else 20.0 * math.log10(linear)


class FakeDevice:
    def __init__(self, active: bool) -> None:
        self.active = active
        self.positions: list[float] = []

    def set_position(self, position: float, *, persist: bool = True) -> None:
        self.positions.append(position)


class Browser(TouchBrowserPrefsMixin):
    """Just enough of the touch UI to run `_apply_volume`."""

    def __init__(self, loader: PatchLoader, device: FakeDevice | None) -> None:
        self.loader = loader
        self.device_volume = device
        self.volume_level = VOLUME_MAX
        self.looper_sends: list[float] = []
        self.saves = 0

    def _send_looper_volume(self, level: float) -> None:
        self.looper_sends.append(level)

    def _save_volume_level(self) -> None:
        self.saves += 1


_TMP = tempfile.TemporaryDirectory()


def tearDownModule():
    _TMP.cleanup()


def _loader(norm_gain_db: float | None = None) -> tuple[PatchLoader, Stage]:
    store = PatchNormalizationStore(Path(_TMP.name) / "norm.json")
    stage = Stage()
    loader = PatchLoader(normalization_store=store, stage_sender=stage)
    loader.osc_client = FakeOscClient()
    loader.osc_enabled = True
    if norm_gain_db is not None:
        loader._norm_db = norm_gain_db
    return loader, stage


def _trim_expected(level: float, norm_db: float) -> float:
    vol = volume_fader_trim_to_db(level, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)
    return -math.inf if vol is None else norm_db + vol


class HeadroomConstant(unittest.TestCase):
    def test_it_is_nine_db_down(self):
        self.assertEqual(dv.DEVICE_MODE_SURGE_TRIM_DB, -9.0)

    def test_it_is_set_in_one_place(self):
        """One authority: no second literal headroom anywhere in the code."""
        defs = []
        for root in ("patch_browser", "scripts"):
            for path in (REPO / root).rglob("*.py"):
                text = path.read_text(errors="replace")
                if re.search(r"^\s*DEVICE_MODE_SURGE_TRIM_DB\s*=", text, re.M):
                    defs.append(path.relative_to(REPO).as_posix())
        self.assertEqual(defs, ["patch_browser/device_volume.py"])


class DeviceMode(unittest.TestCase):
    def test_the_stage_sits_at_the_headroom_whatever_the_fader(self):
        for level in (VOLUME_MIN + 0.01, 0.3, 0.75, VOLUME_MAX):
            with self.subTest(level=level):
                loader, stage = _loader()
                device = FakeDevice(active=True)
                Browser(loader, device)._apply_volume(level)
                self.assertAlmostEqual(stage.db(), dv.DEVICE_MODE_SURGE_TRIM_DB, places=6)
                self.assertEqual(device.positions, [level], "the fader still moves the DAC")
                self.assertEqual(loader.osc_client.messages, [], "Surge's volume is never set")

    def test_normalization_gain_is_unchanged_underneath(self):
        """+9 dB of norm gain and -9 dB of headroom: unity, not a re-clamped gain."""
        loader, stage = _loader(norm_gain_db=9.0)
        Browser(loader, FakeDevice(active=True))._apply_volume(0.5)
        self.assertAlmostEqual(stage.db(), 0.0, places=6)
        self.assertEqual(loader._norm_db, 9.0)

    def test_a_patch_load_keeps_the_headroom(self):
        """refresh_patch_volume re-sends the stage; it must not drop to unity."""
        loader, stage = _loader()
        Browser(loader, FakeDevice(active=True))._apply_volume(0.5)
        loader.refresh_patch_volume("Anything")
        self.assertAlmostEqual(stage.db(), dv.DEVICE_MODE_SURGE_TRIM_DB, places=6)

    def test_the_looper_master_is_left_alone(self):
        loader, _ = _loader()
        browser = Browser(loader, FakeDevice(active=True))
        browser._apply_volume(0.5)
        self.assertEqual(browser.looper_sends, [])


class TrimMode(unittest.TestCase):
    def test_the_fader_is_its_label_on_top_of_the_norm_gain(self):
        for norm in (None, 6.0, -4.0):
            for device in (None, FakeDevice(active=False)):
                for level in (VOLUME_MIN, 0.1, 0.5, 0.9, VOLUME_MAX):
                    with self.subTest(norm=norm, device=device, level=level):
                        loader, stage = _loader(norm_gain_db=norm)
                        browser = Browser(loader, device)
                        browser._apply_volume(level)
                        expected = _trim_expected(level, norm or 0.0)
                        if expected == -math.inf:
                            self.assertEqual(stage.payloads[-1], b"gain 0.00000000")
                        else:
                            self.assertAlmostEqual(stage.db(), expected, places=4)
                        self.assertEqual(browser.looper_sends, [level])

    def test_leaving_device_mode_takes_the_headroom_with_it(self):
        """A card unplugged mid-session: trim mode at full fader is unity again."""
        loader, stage = _loader()
        device = FakeDevice(active=True)
        browser = Browser(loader, device)
        browser._apply_volume(VOLUME_MAX)
        device.active = False
        browser._apply_volume(VOLUME_MAX)
        self.assertEqual(stage.payloads[-1], b"gain 1.00000000")

    def test_set_volume_defaults_to_no_headroom(self):
        loader, stage = _loader()
        loader.set_volume(VOLUME_MAX, headroom_db=dv.DEVICE_MODE_SURGE_TRIM_DB)
        loader.set_volume(VOLUME_MAX)
        self.assertEqual(stage.payloads[-1], b"gain 1.00000000")


if __name__ == "__main__":
    unittest.main()
