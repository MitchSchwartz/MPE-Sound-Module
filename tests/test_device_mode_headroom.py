"""Device mode keeps 6 dB of headroom in Surge; trim mode is untouched.

Since a51a975 (2026-09-13) the Vol fader drives the DAC when the bound card
allows it, and Surge sat at unity. Normalization targets -18 LUFS with a -3 dBTP
peak cap on test notes, so live chords with pressure, and the FX host's added
peaks, reached 0 dBFS on calibrated patches. Device mode now holds Surge at
`DEVICE_MODE_SURGE_TRIM_DB`; the DAC has the analogue range to make it up.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from patch_browser import device_volume as dv
from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_normalization import (
    MAX_AMP_VOLUME_LINEAR,
    NORM_MAX_AMP_VOLUME_LINEAR,
    PatchNormalizationStore,
    db_to_linear,
    volume_fader_to_amp_linear,
)
from patch_browser.touch_browser_prefs import TouchBrowserPrefsMixin
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

REPO = Path(__file__).resolve().parents[1]


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value: float) -> None:
        self.messages.append((address, value))

    def amp(self) -> float:
        a = [v for addr, v in self.messages if addr == "/param/a/amp/volume"]
        b = [v for addr, v in self.messages if addr == "/param/b/amp/volume"]
        assert a and a[-1] == b[-1], "scenes A and B must get the same level"
        return a[-1]


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


def _loader(norm_gain_db: float | None = None) -> PatchLoader:
    store = PatchNormalizationStore(Path(_TMP.name) / "norm.json")
    loader = PatchLoader(normalization_store=store)
    loader.osc_client = FakeOscClient()
    loader.osc_enabled = True
    if norm_gain_db is not None:
        loader._norm_active = True
        loader._patch_gain_linear = db_to_linear(norm_gain_db)
    return loader


def _trim_expected(level: float, loader: PatchLoader) -> float:
    return volume_fader_to_amp_linear(
        level,
        patch_gain_linear=loader._patch_gain_linear,
        cap=NORM_MAX_AMP_VOLUME_LINEAR if loader._norm_active else MAX_AMP_VOLUME_LINEAR,
        fader_min=VOLUME_MIN,
        fader_max=VOLUME_MAX,
        norm_active=loader._norm_active,
    )


class HeadroomConstant(unittest.TestCase):
    def test_it_is_six_db_down(self):
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
    def test_surge_sits_at_the_headroom_whatever_the_fader(self):
        for level in (VOLUME_MIN + 0.01, 0.3, 0.75, VOLUME_MAX):
            with self.subTest(level=level):
                loader = _loader()
                device = FakeDevice(active=True)
                Browser(loader, device)._apply_volume(level)
                self.assertAlmostEqual(loader.osc_client.amp(),
                                       db_to_linear(dv.DEVICE_MODE_SURGE_TRIM_DB), places=12)
                self.assertEqual(device.positions, [level], "the fader still moves the DAC")

    def test_normalization_gain_is_unchanged_underneath(self):
        """+9 dB of norm gain and -9 dB of headroom: unity, not a re-clamped gain."""
        loader = _loader(norm_gain_db=9.0)
        Browser(loader, FakeDevice(active=True))._apply_volume(0.5)
        self.assertAlmostEqual(loader.osc_client.amp(), 1.0, places=12)
        self.assertAlmostEqual(loader._patch_gain_linear, db_to_linear(9.0))

    def test_a_patch_load_keeps_the_headroom(self):
        """refresh_patch_volume re-sends the combined level; it must not drop to unity."""
        loader = _loader()
        Browser(loader, FakeDevice(active=True))._apply_volume(0.5)
        loader.refresh_patch_volume("Anything")
        self.assertAlmostEqual(loader.osc_client.amp(),
                               db_to_linear(dv.DEVICE_MODE_SURGE_TRIM_DB), places=12)

    def test_the_looper_master_is_left_alone(self):
        loader = _loader()
        browser = Browser(loader, FakeDevice(active=True))
        browser._apply_volume(0.5)
        self.assertEqual(browser.looper_sends, [])


class TrimMode(unittest.TestCase):
    def test_fader_math_is_unchanged(self):
        for norm in (None, 6.0, -4.0):
            for device in (None, FakeDevice(active=False)):
                for level in (VOLUME_MIN, 0.1, 0.5, 0.9, VOLUME_MAX):
                    with self.subTest(norm=norm, device=device, level=level):
                        loader = _loader(norm_gain_db=norm)
                        browser = Browser(loader, device)
                        browser._apply_volume(level)
                        self.assertEqual(loader.osc_client.amp(), _trim_expected(level, loader))
                        self.assertEqual(browser.looper_sends, [level])

    def test_leaving_device_mode_takes_the_headroom_with_it(self):
        """A card unplugged mid-session: trim mode at full fader is unity again."""
        loader = _loader()
        device = FakeDevice(active=True)
        browser = Browser(loader, device)
        browser._apply_volume(VOLUME_MAX)
        device.active = False
        browser._apply_volume(VOLUME_MAX)
        self.assertEqual(loader.osc_client.amp(), _trim_expected(VOLUME_MAX, loader))
        self.assertEqual(loader.osc_client.amp(), 1.0)

    def test_set_volume_defaults_to_no_headroom(self):
        """Every other caller (the calibrator among them) measures Surge at unity."""
        loader = _loader()
        loader.set_volume(VOLUME_MAX, headroom_db=dv.DEVICE_MODE_SURGE_TRIM_DB)
        loader.set_volume(VOLUME_MAX)
        self.assertEqual(loader.osc_client.amp(), 1.0)


if __name__ == "__main__":
    unittest.main()
