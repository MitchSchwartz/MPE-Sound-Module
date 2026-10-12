"""Per-patch chain memory (enable mask + one depth), its migration from #150's
per-effect entries, knob pickup, and the target switch (#142 round 3 A, round 4 E)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from patch_browser import fx_host_protocol, fx_policy  # noqa: E402
from patch_browser.fader_pickup import Pickup  # noqa: E402
from patch_browser.fx_policy import (  # noqa: E402
    MOD_WHEEL_EFFECT,
    MOD_WHEEL_PATCH_FIRST,
    STORE_WRITE_DELAY_S,
    Cc1Router,
    FxPolicy,
)
from patch_browser.patch_fx import STORED_TARGETS, PatchFx, PatchFxStore  # noqa: E402
from patch_browser.patch_sidecar_migrate import migrate_sidecar_stores  # noqa: E402
from tests.test_fx_policy import NO_ROUTINGS, WITH_MODWHEEL, _RecordingSender  # noqa: E402

CHORUS = fx_host_protocol.EFFECT_CHORUS
DRIVE = fx_host_protocol.EFFECT_TUBE_DRIVE
PHASER = fx_host_protocol.EFFECT_PHASER
TREMOLO = fx_host_protocol.EFFECT_TREMOLO
#: One-bit masks.
C, D, P, T = (1 << CHORUS), (1 << DRIVE), (1 << PHASER), (1 << TREMOLO)
FIXTURE_150 = REPO / "tests" / "fixtures" / "fx-store-per-effect-150.json"
ROOT = "/surge/patches_factory"


class _Rig(unittest.TestCase):
    """A UI policy with a real store, a remapper router, one clock."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.route = self.root / "fx-route.json"
        self.levels = self.root / "fx-levels.json"
        self.store_path = self.root / "fx.json"
        prefs = self.root / "ui.json"
        patcher = mock.patch("patch_browser.ui_prefs.UI_STATE_FILE", prefs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 1000.0
        self.store = PatchFxStore(self.store_path)
        self.policy = self._policy()
        self.sender = _RecordingSender()
        self.router = Cc1Router(
            sender=self.sender, route_path=self.route, levels_path=self.levels, clock=lambda: self.now
        )
        self.keys = self._patch("Keys", WITH_MODWHEEL)
        self.pad = self._patch("Pad", NO_ROUTINGS)

    def _policy(self, **kw) -> FxPolicy:
        kw.setdefault("enabled", True)
        kw.setdefault("route_path", self.route)
        kw.setdefault("levels_path", self.levels)
        kw.setdefault("global_target", MOD_WHEEL_PATCH_FIRST)
        kw.setdefault("global_mask", C)
        kw.setdefault("store", self.store)
        kw.setdefault("clock", lambda: self.now)
        return FxPolicy(**kw)

    def _patch(self, name: str, body: bytes) -> Path:
        path = self.root / f"{name}.fxp"
        path.write_bytes(body)
        return path

    def load(self, path: Path, policy: FxPolicy | None = None) -> None:
        (policy or self.policy).on_patch_loaded(path, patch_name=path.stem, stable_key=f"factory:Test/{path.stem}")

    def settle(self, seconds: float = 0.2) -> None:
        """Let both loops run: remapper ticks, UI polls."""
        end = self.now + seconds
        while self.now < end:
            self.now += fx_policy.ROUTE_REFRESH_S
            self.router.refresh()
            self.policy.poll_levels()

    def knob(self, *values: int) -> None:
        for value in values:
            self.now += 0.001
            self.router.consume([0xB0, 1, value])
        self.settle()

    def saved(self) -> dict:
        return json.loads(self.store_path.read_text()) if self.store_path.exists() else {}


class PickupTests(unittest.TestCase):
    """The crossing law on the shared anchor (`fader_pickup.Pickup`)."""

    def test_no_change_until_crossed_from_below_then_follows(self) -> None:
        pickup = Pickup()
        self.assertFalse(pickup.catch("k", 10, 64))
        self.assertFalse(pickup.catch("k", 40, 64))
        self.assertTrue(pickup.catch("k", 70, 64))  # crossed between 40 and 70
        self.assertTrue(pickup.catch("k", 20, 64))  # caught: follows anywhere

    def test_no_change_until_crossed_from_above_then_follows(self) -> None:
        pickup = Pickup()
        self.assertFalse(pickup.catch("k", 120, 64))
        self.assertFalse(pickup.catch("k", 90, 64))
        self.assertTrue(pickup.catch("k", 50, 64))
        self.assertTrue(pickup.catch("k", 127, 64))

    def test_landing_within_tolerance_catches_on_the_first_message(self) -> None:
        pickup = Pickup()
        self.assertTrue(pickup.catch("k", 63, 64))

    def test_a_first_message_far_away_never_counts_as_a_crossing(self) -> None:
        pickup = Pickup()
        self.assertFalse(pickup.catch("k", 0, 64))
        self.assertFalse(pickup.catch("k", 0, 64))

    def test_rearm_needs_a_new_crossing(self) -> None:
        pickup = Pickup()
        self.assertTrue(pickup.catch("k", 64, 64))
        pickup.rearm("k")
        self.assertFalse(pickup.catch("k", 100, 30))
        self.assertTrue(pickup.catch("k", 10, 30))
        pickup.rearm()
        self.assertNotIn("k", pickup)
        self.assertFalse(pickup.catch("k", 100, 30))

    def test_the_relative_law_still_reads_the_same_anchor(self) -> None:
        pickup = Pickup()
        self.assertTrue(pickup.first_touch(3, 40))
        self.assertFalse(pickup.first_touch(3, 50))
        self.assertEqual(pickup.previous(3), 40)
        pickup.moved(3, 50)
        self.assertEqual(pickup.previous(3), 50)

    def test_loop_mix_uses_the_shared_anchor(self) -> None:
        """One anchor implementation: loop_mix holds a Pickup, not its own dict."""
        sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))
        import loop_mix

        self.assertIsInstance(loop_mix.LoopMix()._pickup_anchor, Pickup)
        text = (REPO / "scripts" / "sooperlooper" / "loop_mix.py").read_text()
        self.assertNotIn("_pickup_anchor[", text)


class StoreTests(_Rig):
    def test_round_trip_through_the_file(self) -> None:
        fx = PatchFx(mask=D | C, depth=0.25, target=MOD_WHEEL_EFFECT)
        self.store.set("Keys", fx, stable_key="factory:Test/Keys")
        again = PatchFxStore(self.store_path)
        self.assertEqual(again.get("Keys", stable_key="factory:Test/Keys"), fx)
        self.assertEqual(
            self.saved(),
            {"factory:Test/Keys": {"mask": D | C, "depth": 0.25, "target": "effect"}},
        )

    def test_never_set_is_empty(self) -> None:
        self.assertEqual(self.store.get("Nope", stable_key="factory:Test/Nope"), PatchFx())

    def test_an_empty_mask_is_a_value_not_a_default(self) -> None:
        """"No FX" chosen for a patch is kept; it is not "use the default"."""
        self.store.set("Keys", PatchFx(mask=0), stable_key="factory:Test/Keys")
        self.assertEqual(self.saved(), {"factory:Test/Keys": {"mask": 0}})
        self.assertEqual(PatchFxStore(self.store_path).get("Keys", stable_key="factory:Test/Keys").mask, 0)

    def test_nothing_read_from_the_file_is_trusted(self) -> None:
        cases = {
            "mask too big": ({"mask": 16, "depth": 0.5}, PatchFx(depth=0.5)),
            "negative mask": ({"mask": -1}, PatchFx()),
            "bool mask": ({"mask": True}, PatchFx()),
            "float mask": ({"mask": 1.0}, PatchFx()),
            "string mask": ({"mask": "3"}, PatchFx()),
            "depth too big": ({"mask": 3, "depth": 2.0}, PatchFx(mask=3)),
            "nan depth": ({"mask": 3, "depth": float("nan")}, PatchFx(mask=3)),
            "bool depth": ({"mask": 3, "depth": True}, PatchFx(mask=3)),
            "bad target": ({"mask": 3, "target": "sideways"}, PatchFx(mask=3)),
        }
        for label, (entry, want) in cases.items():
            with self.subTest(label):
                self.store_path.write_text(json.dumps({"factory:Test/Keys": entry}))
                got = PatchFxStore(self.store_path).get("Keys", stable_key="factory:Test/Keys")
                self.assertEqual(got, want)
        for bad in ("[]", '"x"', "{"):
            self.store_path.write_text(bad)
            self.assertEqual(PatchFxStore(self.store_path).get("Keys"), PatchFx())

    def test_stored_targets_are_the_policys(self) -> None:
        self.assertEqual(STORED_TARGETS, fx_policy.MOD_WHEEL_TARGETS)

    def test_empty_setup_removes_the_entry(self) -> None:
        self.store.set("Keys", PatchFx(mask=D), stable_key="factory:Test/Keys")
        self.store.set("Keys", PatchFx(), stable_key="factory:Test/Keys")
        self.assertEqual(self.saved(), {})

    def test_no_per_effect_depth_is_left(self) -> None:
        """One depth: the #150 fields are gone from the type and the policy."""
        self.assertEqual({f.name for f in fields(PatchFx)}, {"mask", "depth", "target"})
        for name in ("depth_for", "depths", "effect"):
            self.assertFalse(hasattr(PatchFx(), name), name)
        for name in ("select_effect", "set_global_effect", "effect_id", "effect_name"):
            self.assertFalse(hasattr(FxPolicy, name), name)
        for name in ("EFFECT_PREF_KEY", "DEFAULT_EFFECT", "normalize_effect", "effect_name"):
            self.assertFalse(hasattr(fx_policy, name), name)


class MigrationTests(_Rig):
    """E2: #150 entries become a one-bit mask and that effect's depth."""

    def test_the_150_store_file_migrates(self) -> None:
        """`FIXTURE_150` was written by #150's own code (commit 1488e65): its
        FxPolicy and PatchFxStore, driven through picker choices, fader and
        knob moves and switch flips. Each patch plays what it played under #150:
        the selected effect alone, at the depth #150 stored for that effect."""
        self.store_path.write_bytes(FIXTURE_150.read_bytes())
        store = PatchFxStore(self.store_path)
        want = {
            # Chorus 0.4 and Drive 0.25 stored, Drive selected: Drive at 0.25.
            "Keys/Bonita Keys": PatchFx(mask=D, depth=0.25),
            # Phaser set by the knob: CC 77.
            "Strings/Bowed String": PatchFx(mask=P, depth=0.6062992125984252),
            # Tremolo chosen, never given a depth: Tremolo at the default 0.
            "Pads/Glass Pad": PatchFx(mask=T),
            # Only the switch was ever moved.
            "Leads/Saw Lead": PatchFx(target=MOD_WHEEL_EFFECT),
            # A depth for Tremolo, then Phaser selected: Phaser, never set (0).
            "Bass/Sub Bass": PatchFx(mask=P, target=MOD_WHEEL_EFFECT),
        }
        for rel, fx in want.items():
            with self.subTest(rel):
                self.assertEqual(store.get(Path(rel).name, stable_key=f"factory:{rel}"), fx)
        # The next save writes every entry in the new shape, not just the touched one.
        store.set("Saw Lead", PatchFx(target=MOD_WHEEL_EFFECT), stable_key="factory:Leads/Saw Lead")
        self.assertEqual(
            self.saved(),
            {
                "factory:Keys/Bonita Keys": {"mask": D, "depth": 0.25},
                "factory:Strings/Bowed String": {"mask": P, "depth": 0.6062992125984252},
                "factory:Pads/Glass Pad": {"mask": T},
                "factory:Leads/Saw Lead": {"target": "effect"},
                "factory:Bass/Sub Bass": {"mask": P, "target": "effect"},
            },
        )

    def test_a_migrated_patch_loads_and_sends_its_old_sound(self) -> None:
        self.store_path.write_bytes(FIXTURE_150.read_bytes())
        policy = self._policy(store=PatchFxStore(self.store_path), global_mask=C | T)
        keys = self._patch("Bonita Keys", WITH_MODWHEEL)
        policy.on_patch_loaded(keys, patch_name="Bonita Keys", stable_key="factory:Keys/Bonita Keys")
        self.now += fx_policy.ROUTE_REFRESH_S
        self.router.refresh()
        self.assertEqual((policy.mask, policy.depth), (D, 0.25))
        self.assertEqual(self.sender.sent, [(D, 0.25)])
        # A migrated patch with only a target follows the global mask at 0.
        lead = self._patch("Saw Lead", WITH_MODWHEEL)
        policy.on_patch_loaded(lead, patch_name="Saw Lead", stable_key="factory:Leads/Saw Lead")
        self.assertEqual((policy.mask, policy.depth, policy.target), (C | T, 0.0, MOD_WHEEL_EFFECT))

    def test_old_shapes_one_by_one(self) -> None:
        cases = {
            "selected with its depth": ({"effect": 2, "depths": {"2": 0.5, "0": 0.9}}, PatchFx(mask=P, depth=0.5)),
            "selected without a depth": ({"effect": 3, "depths": {"0": 0.9}}, PatchFx(mask=T)),
            "depths, nothing selected": ({"depths": {"0": 0.9}}, PatchFx()),
            "unknown effect": ({"effect": 9, "depths": {"9": 0.5}, "target": "effect"}, PatchFx(target="effect")),
            "bool effect": ({"effect": True, "depths": {"1": 0.5}}, PatchFx()),
            "bad depth": ({"effect": 1, "depths": {"1": 7}}, PatchFx(mask=D)),
            "depths not a dict": ({"effect": 1, "depths": [0.5]}, PatchFx(mask=D)),
        }
        for label, (entry, want) in cases.items():
            with self.subTest(label):
                self.assertEqual(PatchFx.from_json(entry), want)

    def test_new_shape_wins_over_leftover_old_keys(self) -> None:
        got = PatchFx.from_json({"mask": C | T, "depth": 0.3, "effect": 1, "depths": {"1": 0.9}})
        self.assertEqual(got, PatchFx(mask=C | T, depth=0.3))

    def test_an_entry_that_migrates_to_nothing_is_dropped(self) -> None:
        self.store_path.write_text(json.dumps({"factory:Test/Keys": {"depths": {"0": 0.9}}}))
        store = PatchFxStore(self.store_path)
        store.save()
        self.assertEqual(self.saved(), {})

    def test_global_effect_pref_becomes_the_global_mask(self) -> None:
        prefs = self.root / "ui.json"
        for stored, want in ((DRIVE, D), (TREMOLO, T), (9, C), (True, C), ("1", C)):
            with self.subTest(stored=stored):
                prefs.write_text(json.dumps({"fx_effect": stored, "theme_mode": "dark"}))
                policy = FxPolicy(enabled=True, route_path=self.route, levels_path=self.levels)
                self.assertEqual(policy.global_mask, want)
        prefs.write_text(json.dumps({"fx_effect": DRIVE, "fx_mask": P | T}))
        self.assertEqual(FxPolicy(enabled=True, route_path=self.route, levels_path=self.levels).global_mask, P | T)
        # The first write replaces the old key with the new one.
        prefs.write_text(json.dumps({"fx_effect": DRIVE, "theme_mode": "dark"}))
        policy = FxPolicy(enabled=True, route_path=self.route, levels_path=self.levels)
        self.load(self.keys, policy)
        policy.toggle_effect(CHORUS)
        self.assertEqual(json.loads(prefs.read_text()), {"fx_mask": D | C, "theme_mode": "dark"})


class KeyStabilityTests(_Rig):
    """The existing sidecar key logic, unchanged: stable key, stem fallback, migration."""

    def _p(self, rel: str) -> dict:
        return {"name": Path(rel).stem, "path": f"{ROOT}/{rel}", "stable_key": f"factory:{rel[:-4]}"}

    def test_legacy_stem_entry_is_read_and_rewritten_under_the_stable_key(self) -> None:
        self.store_path.write_text(json.dumps({"Keys": {"mask": D}}))
        store = PatchFxStore(self.store_path)
        self.assertEqual(store.get("Keys", stable_key="factory:Keys/Keys").mask, D)
        store.set("Keys", PatchFx(mask=P), stable_key="factory:Keys/Keys")
        self.assertEqual(json.loads(self.store_path.read_text()), {"factory:Keys/Keys": {"mask": P}})

    def test_post_scan_migration_moves_unambiguous_stems(self) -> None:
        # #150-shaped stem entries: migrated in shape and in key.
        self.store_path.write_text(json.dumps({"Keys": {"effect": DRIVE}, "Lead": {"effect": PHASER}}))
        store = PatchFxStore(self.store_path)
        patches = [self._p("Keys/Keys.fxp"), self._p("Bass/Lead.fxp"), self._p("Pad/Lead.fxp")]
        warnings = migrate_sidecar_stores(fx=store, patches=patches, patch_dirs=[Path(ROOT)])
        saved = json.loads(self.store_path.read_text())
        self.assertEqual(saved["factory:Keys/Keys"], {"mask": D})
        self.assertIn("Lead", saved)  # ambiguous: kept, not merged
        self.assertTrue(any("PatchFxStore" in w and "ambiguous" in w for w in warnings))

    def test_a_moved_or_renamed_patch_starts_from_the_defaults(self) -> None:
        """Same as Hold and normalization: the key is the patch's path in its root."""
        self.store.set("Keys", PatchFx(mask=D, depth=0.7), stable_key="factory:Keys/Keys")
        self.assertEqual(self.store.get("Keys", stable_key="factory:Keys/Keys").mask, D)
        self.assertEqual(self.store.get("Keys", stable_key="factory:Other/Keys"), PatchFx())
        self.assertEqual(self.store.get("Keys 2", stable_key="factory:Keys/Keys 2"), PatchFx())

    def test_loader_scanner_and_migration_carry_the_store(self) -> None:
        from patch_browser.patch_loader import PatchLoader
        from patch_browser.patch_scanner import PatchScanner
        from patch_browser.patch_sidecar_migrate import migrate_loader_sidecars

        loader = PatchLoader(fx_policy=self.policy, fx_store=self.store)
        self.assertIs(loader.fx_store, self.store)
        self.assertIs(self.policy._store, self.store)
        scanner = PatchScanner.__new__(PatchScanner)
        scanner.patch_dirs = [Path(ROOT)]
        scanner.bind_sidecar_loader(loader)
        self.assertEqual(self.store._patch_dirs, (Path(ROOT),))
        with mock.patch.object(self.store, "migrate_stem_keys", return_value=[]) as migrate:
            migrate_loader_sidecars(loader, [], [Path(ROOT)])
        migrate.assert_called_once()


class PerPatchMemoryTests(_Rig):
    def test_never_set_patch_gets_the_global_mask_at_depth_0(self) -> None:
        self.policy.set_global_mask(P | T)
        self.load(self.keys)
        self.settle()
        self.assertEqual((self.policy.mask, self.policy.depth), (P | T, 0.0))
        self.assertEqual(self.sender.sent[-1], (P | T, 0.0))
        self.policy.flush_store()
        self.assertEqual(self.saved(), {})  # loading writes nothing

    def test_each_patch_remembers_mask_depth_and_target(self) -> None:
        self.load(self.keys)
        self.policy.toggle_effect(DRIVE)  # chorus + drive
        self.policy.set_depth(0.5, final=True)
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.load(self.pad)
        self.policy.toggle_effect(CHORUS)  # the global is now C|D: off leaves drive
        self.policy.toggle_effect(TREMOLO)
        self.policy.set_depth(0.2, final=True)
        self.load(self.keys)
        self.settle()
        self.assertEqual(self.policy.mask, C | D)
        self.assertEqual(self.policy.depth, 0.5)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(self.sender.sent[-1], (C | D, 0.5))
        # A fresh UI process reads the same back from the file.
        self.policy.flush_store()
        fresh = self._policy(store=PatchFxStore(self.store_path))
        self.load(self.pad, fresh)
        self.assertEqual((fresh.mask, fresh.depth, fresh.target), (D | T, 0.2, MOD_WHEEL_PATCH_FIRST))
        self.load(self.keys, fresh)
        self.assertEqual((fresh.mask, fresh.depth, fresh.target), (C | D, 0.5, MOD_WHEEL_EFFECT))
        self.assertEqual(
            self.saved(),
            {
                "factory:Test/Keys": {"mask": C | D, "depth": 0.5, "target": "effect"},
                "factory:Test/Pad": {"mask": D | T, "depth": 0.2},
            },
        )

    def test_patch_target_wins_over_the_settings_default(self) -> None:
        self.load(self.keys)
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.policy.set_global_default(MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.load(self.pad)  # never set: follows the default
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)

    def test_toggling_moves_the_mask_and_nothing_else(self) -> None:
        self.load(self.keys)
        self.settle()
        self.policy.set_depth(0.3, final=True)
        self.settle()
        stamp = json.loads(self.route.read_text())["route_stamp"]
        for effect, want in ((DRIVE, C | D), (PHASER, C | D | P), (CHORUS, D | P), (DRIVE, P), (PHASER, 0)):
            with self.subTest(effect=effect):
                self.policy.toggle_effect(effect)
                self.settle()
                self.assertEqual(self.policy.mask, want)
                self.assertEqual(self.policy.depth, 0.3)
                self.assertEqual(self.router.depth, 0.3)
                self.assertEqual(self.sender.sent[-1], (want, 0.3))
                route = json.loads(self.route.read_text())
                self.assertEqual((route["mask"], route["route_stamp"]), (want, stamp))
        self.policy.flush_store()
        self.assertEqual(self.saved()["factory:Test/Keys"], {"mask": 0, "depth": 0.3})

    def test_a_tap_on_one_row_twice_is_where_it_started(self) -> None:
        self.load(self.keys)
        for effect in fx_host_protocol.EFFECT_IDS:
            with self.subTest(effect=effect):
                before = self.policy.mask
                self.policy.toggle_effect(effect)
                self.assertNotEqual(self.policy.mask, before)
                self.policy.toggle_effect(effect)
                self.assertEqual(self.policy.mask, before)

    def test_bad_ids_toggle_nothing(self) -> None:
        self.load(self.keys)
        for bad in (9, -1, True, "1", None):
            with self.subTest(bad=bad):
                self.policy.toggle_effect(bad)
                self.assertEqual(self.policy.mask, C)

    def test_picking_also_sets_the_default_for_never_set_patches(self) -> None:
        self.load(self.keys)
        self.policy.toggle_effect(TREMOLO)
        self.load(self.pad)
        self.assertEqual((self.policy.mask, self.policy.depth), (C | T, 0.0))

    def test_a_depth_pins_the_mask(self) -> None:
        """Else a later change of the global default would play it on another chain."""
        self.load(self.keys)
        self.policy.set_depth(0.6, final=True)
        self.policy.set_global_mask(D)
        self.assertEqual((self.policy.mask, self.policy.depth), (C, 0.6))
        self.load(self.pad)  # never set: the new default
        self.assertEqual((self.policy.mask, self.policy.depth), (D, 0.0))


class StoreWriteTimingTests(_Rig):
    def test_writes_once_after_the_moves_stop_never_per_tick(self) -> None:
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.settle()
        with mock.patch.object(self.store, "save", wraps=self.store.save) as save:
            self.knob(0)
            for value in range(0, 128, 4):  # a sweep, 20 ms apart
                self.now += 0.02
                self.router.consume([0xB0, 1, value])
                self.router.refresh()
                self.policy.poll_levels()
            self.settle(STORE_WRITE_DELAY_S / 2)
            self.assertEqual(save.call_count, 0)
            for step in range(8):  # a fader drag
                self.policy.set_depth(step / 10, final=step == 7)
                self.settle(0.05)
            self.policy.finish_depth()
            self.assertEqual(save.call_count, 0)
            self.settle(STORE_WRITE_DELAY_S + 0.2)
            self.assertEqual(save.call_count, 1)
            self.settle(STORE_WRITE_DELAY_S * 3)
            self.assertEqual(save.call_count, 1)
        self.assertEqual(self.saved()["factory:Test/Keys"], {"mask": C, "depth": 0.7})

    def test_a_pending_change_is_written_before_the_next_load(self) -> None:
        self.load(self.keys)
        self.policy.set_depth(0.4, final=True)
        self.load(self.pad)
        self.assertEqual(self.saved()["factory:Test/Keys"], {"mask": C, "depth": 0.4})

    def test_shutdown_flush_writes_what_is_pending(self) -> None:
        self.load(self.keys)
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.assertEqual(self.saved(), {})
        self.assertTrue(self.policy.flush_store())
        self.assertEqual(self.saved()["factory:Test/Keys"], {"target": "effect"})
        self.assertFalse(self.policy.flush_store())

    def test_off_writes_nothing(self) -> None:
        policy = self._policy(enabled=False)
        self.load(self.keys, policy)
        policy.set_depth(0.5, final=True)
        policy.set_target(MOD_WHEEL_EFFECT)
        policy.toggle_effect(DRIVE)
        policy.set_global_mask(D)
        self.assertEqual(policy.mask, C)
        policy.flush_store()
        self.assertFalse(self.store_path.exists())


class PickupInTheRouterTests(_Rig):
    """A3: after any switch the knob does nothing until it crosses the value in force."""

    def test_effect_knob_after_a_patch_switch_both_directions(self) -> None:
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.policy.set_depth(0.5, final=True)  # 63.5 in CC units
        self.load(self.pad)
        self.settle()
        self.knob(0, 64)  # pad never set: depth 0, picked up at 0
        self.assertAlmostEqual(self.policy.depth, 64 / 127)
        self.load(self.keys)  # knob physically at 64... (0.5 stored)
        self.settle()
        self.knob(20)  # far below 63.5 now
        self.load(self.pad)
        self.settle()
        sent = len(self.sender.sent)
        self.knob(100, 90)  # above pad's 64, approaching from above, not crossed
        self.assertEqual(len(self.sender.sent), sent)
        self.assertAlmostEqual(self.policy.depth, 64 / 127)
        self.knob(60)  # crossed 64 coming down: follows from here
        self.assertEqual(self.sender.sent[-1], (C, 60 / 127))
        self.knob(30)
        self.assertEqual(self.sender.sent[-1], (C, 30 / 127))
        self.load(self.keys)
        self.settle()
        self.assertEqual(self.policy.depth, 0.5)
        sent = len(self.sender.sent)
        self.knob(40, 60)  # below 63.5, rising, not yet crossed
        self.assertEqual(len(self.sender.sent), sent)
        self.knob(70)  # crossed going up
        self.assertEqual(self.sender.sent[-1], (C, 70 / 127))

    def test_patch_mod_knob_is_swallowed_until_it_crosses(self) -> None:
        self.load(self.keys)  # routes the wheel: CC 1 to Surge
        self.policy.set_patch_mod(80)
        self.settle()
        self.assertFalse(self.router.consume([0xB0, 1, 79]))  # reached (tolerance)
        self.policy.set_patch_mod(20)  # the fader moved it from under the knob
        self.settle()
        self.assertTrue(self.router.consume([0xB0, 1, 79]))  # swallowed
        self.assertTrue(self.router.consume([0xB0, 1, 50]))
        self.assertFalse(self.router.consume([0xB0, 1, 10]))  # crossed 20
        self.assertFalse(self.router.consume([0xB0, 1, 30]))
        self.settle()
        self.assertEqual(self.policy.patch_mod, 30)

    def test_a_fader_move_rearms_it(self) -> None:
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.settle()
        self.knob(0, 50)
        self.policy.set_depth(0.9, final=True)
        self.settle()
        sent = len(self.sender.sent)
        self.knob(52)  # the fader moved the depth from under the knob
        self.assertEqual(len(self.sender.sent), sent)
        self.assertEqual(self.policy.depth, 0.9)
        self.knob(120)  # crossed 0.9 * 127
        self.assertEqual(self.sender.sent[-1], (C, 120 / 127))

    def test_a_toggle_does_not_rearm_it(self) -> None:
        """Toggling moves no depth, so a knob that had caught the depth keeps
        following it (and one that had not still has to cross it)."""
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.settle()
        self.knob(0, 50)
        self.policy.toggle_effect(DRIVE)
        self.settle()
        self.assertEqual(self.sender.sent[-1], (C | D, 50 / 127))
        self.knob(60)
        self.assertEqual(self.sender.sent[-1], (C | D, 60 / 127))
        self.load(self.pad)  # depth 0: re-armed by the load
        self.settle()
        self.knob(70)
        self.policy.toggle_effect(TREMOLO)
        self.settle()
        self.knob(65)  # still not crossed 0
        self.assertEqual(self.policy.depth, 0.0)
        self.assertEqual(self.sender.sent[-1], (C | D | T, 0.0))

    def test_no_effect_enabled_the_knob_still_follows_the_rules(self) -> None:
        """E3: with the empty mask the knob moves the depth (inaudibly) under
        the same pickup, and an effect switched on then plays at it."""
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.policy.toggle_effect(CHORUS)
        self.settle()
        self.assertEqual(self.policy.mask, 0)
        self.assertEqual(self.sender.sent[-1], (0, 0.0))
        self.knob(40)  # not crossed 0
        self.assertEqual(self.sender.sent[-1], (0, 0.0))
        self.knob(0, 64)
        self.assertEqual(self.sender.sent[-1], (0, 64 / 127))
        self.assertAlmostEqual(self.policy.depth, 64 / 127)
        self.policy.toggle_effect(PHASER)
        self.settle()
        self.assertEqual(self.sender.sent[-1], (P, 64 / 127))

    def test_faders_show_the_stored_values_while_the_knob_is_elsewhere(self) -> None:
        self.load(self.keys)
        self.policy.toggle_effect(PHASER)
        self.policy.set_depth(0.25, final=True)
        self.policy.set_patch_mod(40)
        self.load(self.pad)
        self.settle()
        self.knob(0, 127)
        self.load(self.keys)
        self.settle()
        self.knob(126, 125)  # not picked up
        self.assertEqual(self.policy.mask, C | P)
        self.assertEqual(self.policy.depth, 0.25)
        self.assertEqual(self.policy.patch_mod, 40)


class TargetSwitchTests(_Rig):
    """A4: switching the controller only moves the controller."""

    def test_flip_never_zeroes_or_resets_either_value(self) -> None:
        self.load(self.keys)  # patch first, wheel routed: Surge
        self.policy.set_depth(0.6, final=True)
        self.policy.set_patch_mod(90, final=True)
        self.settle()
        self.knob(89, 10)  # the knob drives Surge down to 10
        self.assertEqual(self.policy.patch_mod, 10)
        self.policy.set_patch_mod(90, final=True)
        self.settle()
        self.knob(0)  # knob parked at 0, far from everything
        sent = list(self.sender.sent)
        for target in (MOD_WHEEL_EFFECT, MOD_WHEEL_PATCH_FIRST, MOD_WHEEL_EFFECT, MOD_WHEEL_PATCH_FIRST):
            with self.subTest(target=target):
                self.policy.set_target(target)
                self.settle()
                self.assertEqual(self.policy.depth, 0.6)
                self.assertEqual(self.policy.patch_mod, 90)
                self.assertEqual(self.router.depth, 0.6)
                self.assertEqual(self.router.patch_mod, 90)
                self.assertEqual(self.sender.sent, sent)  # the effect was told nothing
                self.knob(0)  # and the parked knob changes neither
                self.assertEqual((self.policy.depth, self.policy.patch_mod), (0.6, 90))

    def test_a_knob_move_after_a_fader_move_wins_even_if_read_first(self) -> None:
        """The fader's value reaches the remapper late; Surge got the knob after it."""
        self.load(self.keys)
        self.policy.set_patch_mod(40, final=True)
        self.settle()
        self.knob(40, 60)
        self.policy.set_patch_mod(90, final=True)  # fader: Surge has 90...
        self.now += 0.001
        self.assertFalse(self.router.consume([0xB0, 1, 61]))  # ...then knob: Surge has 61
        self.settle()
        self.assertEqual(self.router.patch_mod, 61)
        self.assertEqual(self.policy.patch_mod, 61)

    def test_surge_is_sent_nothing_on_a_flip(self) -> None:
        """No replay: the remapper has no path to Surge but the live knob."""
        self.assertFalse(hasattr(Cc1Router, "_forward"))
        self.load(self.keys)
        self.policy.set_patch_mod(70, final=True)
        self.settle()
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.settle()
        self.policy.set_target(MOD_WHEEL_PATCH_FIRST)
        self.settle()
        self.assertFalse(self.levels.exists() and "patch_mod" in json.loads(self.levels.read_text()))

    def test_patch_load_resends_the_patch_mod_value_to_surge(self) -> None:
        from patch_browser.patch_loader import PatchLoader

        loader = PatchLoader.__new__(PatchLoader)
        loader.fx_policy = self.policy
        loader.osc_enabled = True
        loader.osc_client = mock.Mock()
        loader._resend_patch_mod()
        loader.osc_client.send_message.assert_not_called()  # not known yet: nothing
        self.policy.set_patch_mod(55)
        loader._resend_patch_mod()
        loader.osc_client.send_message.assert_called_once_with("/cc", [0.0, 1.0, 55.0])


class PickerRowTests(unittest.TestCase):
    """A5: the picker's rows can take a per-row settings button without a new layout."""

    def setUp(self) -> None:
        from tests.fake_pygame import install_fake_pygame

        install_fake_pygame()

    def test_no_button_today_the_row_is_the_choice(self) -> None:
        from patch_browser.geometry import Rect
        from patch_browser.touch_browser_fx_effect_modal import FX_EFFECT_ROW_SETTINGS_W, split_effect_row

        row = Rect(10, 20, 300, 48)
        self.assertEqual(FX_EFFECT_ROW_SETTINGS_W, 0)
        self.assertEqual(split_effect_row(row), (row, None))

    def test_a_settings_width_splits_the_row(self) -> None:
        from patch_browser.geometry import Rect
        from patch_browser.touch_browser_fx_effect_modal import split_effect_row

        row = Rect(10, 20, 300, 48)
        choice, settings = split_effect_row(row, 48)
        self.assertEqual(settings.w, 48)
        self.assertEqual(settings.right, row.right)
        self.assertLess(choice.right, settings.x)
        self.assertEqual((choice.y, choice.h, settings.y, settings.h), (row.y, row.h, row.y, row.h))


if __name__ == "__main__":
    unittest.main()
