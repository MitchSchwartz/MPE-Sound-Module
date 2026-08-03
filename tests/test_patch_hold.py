"""Tests for per-patch Hold multiplier store and loader OSC apply."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.patch_hold import (
    DEFAULT_HOLD_MULT,
    PatchHoldStore,
    effective_aeg_value,
    hold_mult_to_offset,
    hold_offset_to_mult,
)
from patch_browser.patch_loader import PatchLoader


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value) -> None:
        if isinstance(value, list):
            return
        self.messages.append((address, float(value)))


class PatchHoldStoreTests(unittest.TestCase):
    def test_effective_mult_defaults_to_one(self) -> None:
        store = PatchHoldStore(Path("/tmp/unused-hold.json"))
        self.assertEqual(store.get_effective_hold_mult("Lead"), DEFAULT_HOLD_MULT)

    def test_user_mult_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hold.json"
            store = PatchHoldStore(path)
            store.set_user_hold_mult("Pad", 1.5)
            store.load()
            self.assertAlmostEqual(store.get_effective_hold_mult("Pad"), 1.5)
            saved = json.loads(path.read_text())
            self.assertAlmostEqual(saved["Pad"]["user_hold_mult"], 1.5)

    def test_clear_user_mult(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hold.json"
            store = PatchHoldStore(path)
            store.set_baseline(
                "Pad",
                {
                    "a": {"sustain": 0.8, "decay": 0.2, "release": 0.3},
                    "b": {"sustain": 0.7, "decay": 0.2, "release": 0.3},
                },
            )
            store.set_user_hold_mult("Pad", 2.0)
            store.clear_user_hold_mult("Pad")
            self.assertEqual(store.get_effective_hold_mult("Pad"), DEFAULT_HOLD_MULT)
            saved = json.loads(path.read_text())
            self.assertNotIn("user_hold_mult", saved["Pad"])

    def test_effective_aeg_value_clamps(self) -> None:
        self.assertAlmostEqual(effective_aeg_value(0.5, 2.0), 1.0)
        self.assertAlmostEqual(effective_aeg_value(0.8, 0.25), 0.2)

    def test_hold_offset_round_trip(self) -> None:
        self.assertAlmostEqual(hold_mult_to_offset(DEFAULT_HOLD_MULT), 0.0)
        self.assertAlmostEqual(hold_offset_to_mult(0.0), DEFAULT_HOLD_MULT)
        self.assertAlmostEqual(hold_offset_to_mult(-0.45), 0.25)
        self.assertAlmostEqual(hold_offset_to_mult(0.45), 4.0)

    def test_format_hold_offset(self) -> None:
        store = PatchHoldStore(Path("/tmp/unused-hold-fmt.json"))
        self.assertEqual(store.format_hold_offset(0.0), "0")
        self.assertEqual(store.format_hold_offset(0.12), "+12")
        self.assertEqual(store.format_hold_offset(-0.08), "-8")


class PatchLoaderHoldTests(unittest.TestCase):
    def test_refresh_hold_scales_sustain_decay_release_not_attack(self) -> None:
        hold = PatchHoldStore(Path("/tmp/unused-hold2.json"))
        hold.set_baseline(
            "Lead",
            {
                "a": {"sustain": 0.8, "decay": 0.4, "release": 0.5},
                "b": {"sustain": 0.6, "decay": 0.3, "release": 0.2},
            },
        )
        hold.set_user_hold_mult("Lead", 2.0)
        loader = PatchLoader(hold_store=hold)
        loader.osc_client = FakeOscClient()
        loader.osc_enabled = True

        self.assertTrue(loader.refresh_hold("Lead"))
        paths = [addr for addr, _val in loader.osc_client.messages]
        self.assertIn("/param/a/aeg/sustain", paths)
        self.assertIn("/param/a/aeg/decay", paths)
        self.assertIn("/param/a/aeg/release", paths)
        self.assertNotIn("/param/a/aeg/attack", paths)

        by_path = dict(loader.osc_client.messages)
        self.assertAlmostEqual(by_path["/param/a/aeg/sustain"], 1.0)
        self.assertAlmostEqual(by_path["/param/a/aeg/decay"], 0.8)
        self.assertAlmostEqual(by_path["/param/b/aeg/release"], 0.4)

    def test_parse_surge_param_query_percent_string(self) -> None:
        sample = bytes.fromhex(
            "2f706172616d2f612f6165672f7375737461696e000000002c6673003ecaf8be33392e3634202500"
        )
        value = PatchLoader._parse_surge_param_query(sample)
        self.assertIsNotNone(value)
        assert value is not None
        self.assertAlmostEqual(value, 0.3964, places=3)

    def test_load_patch_captures_baseline_and_applies_mult(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hold_path = Path(tmp) / "hold.json"
            hold = PatchHoldStore(hold_path)
            loader = PatchLoader(hold_store=hold)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True

            query_values = {
                "/param/a/aeg/sustain": 0.5,
                "/param/a/aeg/decay": 0.25,
                "/param/a/aeg/release": 0.3,
                "/param/b/aeg/sustain": 0.4,
                "/param/b/aeg/decay": 0.2,
                "/param/b/aeg/release": 0.35,
            }

            def fake_query(path: str) -> float | None:
                return query_values.get(path)

            with mock.patch.object(loader, "_query_osc_float", side_effect=fake_query):
                with mock.patch("patch_browser.patch_loader.time.sleep"):
                    ok = loader.load_patch("/patches/Lead.fxp")

            self.assertTrue(ok)
            baseline = hold.get_baseline("Lead")
            self.assertIsNotNone(baseline)
            assert baseline is not None
            self.assertAlmostEqual(baseline["a"]["sustain"], 0.5)
            hold.set_user_hold_mult("Lead", 2.0)
            loader.refresh_hold("Lead")
            by_path = dict(loader.osc_client.messages)
            self.assertAlmostEqual(by_path["/param/a/aeg/sustain"], 1.0)


if __name__ == "__main__":
    unittest.main()
