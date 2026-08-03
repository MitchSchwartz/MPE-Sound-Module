"""Tests for reusable mixer fader controls."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.mixer_controls import NormControl, TouchControl, mixer_control_by_id
from patch_browser.patch_pressure import (
    PatchPressureStore,
    TOUCH_DISPLAY_MAX,
    TOUCH_DISPLAY_MIN,
    cal_floor_to_touch_anchor,
    touch_fader_value,
)
from patch_browser.touch_ui_constants import FADER_HANDLE_H


class _Track:
    def __init__(self, y: int, h: int) -> None:
        self.y = y
        self.h = h


def _handle_y(min_value: float, max_value: float, track: _Track, value: float) -> int:
    span = max_value - min_value
    ratio = 0.0 if span <= 0 else (value - min_value) / span
    ratio = max(0.0, min(1.0, ratio))
    travel = track.h - FADER_HANDLE_H
    return int(track.y + travel * (1.0 - ratio))


class _FakeBrowser:
    def __init__(self) -> None:
        self.detail_patch = {"name": "Fake Ethno", "category": "Quick Select"}
        self.loaded_patch_info = None
        self.loader = mock.Mock()
        self.loader.osc_enabled = False
        self._toasts: list[str] = []

    def _toast(self, message: str, _duration: float = 1.0) -> None:
        self._toasts.append(message)


class TouchControlTests(unittest.TestCase):
    def test_untrimmed_shows_calibration_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Fake Ethno", 0.9, -43.0)
            browser = _FakeBrowser()
            browser.loader.pressure = store
            control = TouchControl()
            self.assertEqual(control.read(browser), 50.0)
            self.assertEqual(control.default(browser), 50.0)

    def test_trimmed_patch_can_read_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Lead", 0.4, -30.0)
            store.set_user_touch_offset("Lead", -0.9)
            browser = _FakeBrowser()
            browser.detail_patch = {"name": "Lead", "category": "Quick Select"}
            browser.loader.pressure = store
            control = TouchControl()
            self.assertAlmostEqual(control.read(browser), -28.0, delta=0.5)

    def test_user_override_relative_to_cal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Fake Ethno", 0.9, -43.0)
            store.set_user_touch_offset("Fake Ethno", -0.5)
            browser = _FakeBrowser()
            browser.loader.pressure = store
            control = TouchControl()
            self.assertAlmostEqual(control.read(browser), 22.2, places=1)

    def test_zero_cal_reads_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Rhody EP", 0.0, -16.0)
            browser = _FakeBrowser()
            browser.detail_patch = {"name": "Rhody EP", "category": "Quick Select"}
            browser.loader.pressure = store
            control = TouchControl()
            self.assertEqual(control.read(browser), 0.0)

    def test_reset_toasts_calibration_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Fake Ethno", 0.9, -43.0)
            store.set_user_touch_offset("Fake Ethno", -0.5)
            browser = _FakeBrowser()
            browser.loader.pressure = store
            control = TouchControl()
            control.reset(browser)
            self.assertEqual(browser._toasts[-1], "Touch reset to +50")
            self.assertEqual(control.read(browser), 50.0)


class TouchFaderTrackTests(unittest.TestCase):
    def test_spec_range_is_bipolar(self) -> None:
        control = TouchControl()
        self.assertEqual(control.spec.min_value, -50.0)
        self.assertEqual(control.spec.max_value, 50.0)
        self.assertEqual(TOUCH_DISPLAY_MIN, -50.0)
        self.assertEqual(TOUCH_DISPLAY_MAX, 50.0)

    def test_cal_anchor_maps_zero_floor_to_zero(self) -> None:
        self.assertEqual(cal_floor_to_touch_anchor(0.0), 0.0)

    def test_minus_fifty_on_full_track(self) -> None:
        track = _Track(y=20, h=200)
        bottom = track.y + track.h - FADER_HANDLE_H
        self.assertEqual(_handle_y(TOUCH_DISPLAY_MIN, TOUCH_DISPLAY_MAX, track, -50.0), bottom)

    def test_touch_fader_value_matches_anchor_plus_trim(self) -> None:
        self.assertAlmostEqual(touch_fader_value(0.9, -0.5), 22.2, places=1)


class MixerControlRegistryTests(unittest.TestCase):
    def test_lookup_by_channel_id(self) -> None:
        browser = _FakeBrowser()
        self.assertIsNotNone(mixer_control_by_id(browser, "touch"))
        self.assertIsNotNone(mixer_control_by_id(browser, "norm"))
        self.assertIsInstance(mixer_control_by_id(browser, "norm"), NormControl)


if __name__ == "__main__":
    unittest.main()
