"""Tests for path-based stable_key sidecar stores and migration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from patch_browser.patch_hold import PatchHoldStore
from patch_browser.patch_normalization import PatchNormalizationStore
from patch_browser.patch_pressure import PatchPressureStore
from patch_browser.patch_sidecar_key import (
    PatchRef,
    build_stem_to_stable_keys,
    lookup_entry,
    migrate_sidecar_data,
    patch_refs_match,
    resolve_storage_key,
)
from patch_browser.patch_sidecar_migrate import migrate_sidecar_stores


def _patch(name: str, rel: str, root: str = "factory") -> dict:
    stable = f"{root}:{rel.replace('.fxp', '')}"
    return {
        "name": name,
        "path": f"/surge/patches_factory/{rel}",
        "stable_key": stable,
        "category": rel.split("/")[0],
    }


class PatchSidecarKeyTests(unittest.TestCase):
    def test_resolve_storage_key_prefers_stable_key(self) -> None:
        key = resolve_storage_key(
            "Lead",
            patch_path="/surge/patches_factory/Bass/Lead.fxp",
            stable_key="factory:Bass/Lead",
        )
        self.assertEqual(key, "factory:Bass/Lead")

    def test_lookup_falls_back_to_stem(self) -> None:
        data = {"Lead": {"gain_db": 3.0, "enabled": True}}
        entry, matched = lookup_entry(
            data,
            "Lead",
            stable_key="factory:Bass/Lead",
        )
        self.assertIsNotNone(entry)
        self.assertEqual(matched, "Lead")

    def test_patch_refs_match_by_stable_key(self) -> None:
        a = PatchRef("Lead", stable_key="factory:Bass/Lead")
        b = PatchRef("Lead", stable_key="factory:Bass/Lead")
        c = PatchRef("Lead", stable_key="factory:Pad/Lead")
        self.assertTrue(patch_refs_match(a, b))
        self.assertFalse(patch_refs_match(a, c))


class SidecarMigrationTests(unittest.TestCase):
    def test_unambiguous_stem_migrates(self) -> None:
        patches = [
            _patch("Deep Growl", "Bass/Sub/Deep Growl.fxp"),
        ]
        stem_map = build_stem_to_stable_keys(patches)
        data = {"Deep Growl": {"user_hold_mult": 1.5}}
        new_data, warnings, changed = migrate_sidecar_data(data, stem_map)
        self.assertTrue(changed)
        self.assertEqual(warnings, [])
        self.assertIn("factory:Bass/Sub/Deep Growl", new_data)
        self.assertNotIn("Deep Growl", new_data)

    def test_ambiguous_stem_kept_with_warning(self) -> None:
        patches = [
            _patch("Lead", "Bass/Lead.fxp"),
            _patch("Lead", "Pad/Lead.fxp"),
        ]
        stem_map = build_stem_to_stable_keys(patches)
        data = {"Lead": {"gain_db": 1.0, "enabled": True}}
        new_data, warnings, changed = migrate_sidecar_data(data, stem_map)
        self.assertFalse(changed)
        self.assertIn("Lead", new_data)
        self.assertTrue(any("ambiguous stem" in w for w in warnings))

    def test_store_write_uses_stable_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "norm.json"
            store = PatchNormalizationStore(path)
            store.set_enabled(
                "Deep Growl",
                False,
                stable_key="factory:Bass/Sub/Deep Growl",
            )
            saved = json.loads(path.read_text())
            self.assertIn("factory:Bass/Sub/Deep Growl", saved)
            self.assertFalse(saved["factory:Bass/Sub/Deep Growl"]["enabled"])

    def test_store_reads_stem_writes_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hold.json"
            path.write_text(json.dumps({"Lead": {"user_hold_mult": 2.0}}))
            store = PatchHoldStore(path)
            mult = store.get_effective_hold_mult(
                "Lead",
                stable_key="factory:Bass/Lead",
            )
            self.assertAlmostEqual(mult, 2.0)
            store.set_user_hold_mult(
                "Lead",
                1.25,
                stable_key="factory:Bass/Lead",
            )
            saved = json.loads(path.read_text())
            self.assertIn("factory:Bass/Lead", saved)
            self.assertNotIn("Lead", saved)

    def test_migrate_sidecar_stores_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            norm_path = Path(tmp) / "norm.json"
            hold_path = Path(tmp) / "hold.json"
            pressure_path = Path(tmp) / "pressure.json"
            norm_path.write_text(json.dumps({"Acid": {"gain_db": 5.0, "enabled": True}}))
            patches = [_patch("Acid", "Lead/Acid.fxp")]
            norm = PatchNormalizationStore(norm_path)
            hold = PatchHoldStore(hold_path)
            pressure = PatchPressureStore(pressure_path)
            warnings = migrate_sidecar_stores(
                normalization=norm,
                hold=hold,
                pressure=pressure,
                patches=patches,
                patch_dirs=[Path("/surge/patches_factory")],
            )
            self.assertEqual(warnings, [])
            saved = json.loads(norm_path.read_text())
            self.assertIn("factory:Lead/Acid", saved)


if __name__ == "__main__":
    unittest.main()
