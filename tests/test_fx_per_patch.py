"""Per-patch chain memory (enable mask + one depth), its migration from #150's
per-effect entries, knob pickup, and the target switch (#142 round 3 A, round 4 E,
round 5 F1-F3)."""

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
from patch_browser.fader_pickup import Pickup, scaled  # noqa: E402
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

#: Every level 1.0, as a stored entry (list) and as PatchFx holds it (tuple).
ONES = [1.0] * len(fx_host_protocol.EFFECT_IDS)
ONES_T = tuple(ONES)

CHORUS = fx_host_protocol.EFFECT_CHORUS
DRIVE = fx_host_protocol.EFFECT_TUBE_DRIVE
PHASER = fx_host_protocol.EFFECT_PHASER
TREMOLO = fx_host_protocol.EFFECT_TREMOLO
#: One-bit masks.
C, D, P, T = (1 << CHORUS), (1 << DRIVE), (1 << PHASER), (1 << TREMOLO)
FLANGER = fx_host_protocol.EFFECT_FLANGER
FIXTURE_150 = REPO / "tests" / "fixtures" / "fx-store-per-effect-150.json"
#: Entries as rounds 3/4 left them on the appliance: a mask with no depth (the
#: depth then came from nowhere, 0 by luck), a mask and depth, all three.
FIXTURE_ROUND4 = REPO / "tests" / "fixtures" / "fx-store-round4-partial.json"
#: Written by the store as it was before per-effect levels (origin/dev at
#: 4a1e29e, its own FxPolicy and PatchFxStore driven through toggles, depths
#: and a switch flip): every entry has a mask and a depth, none has levels.
FIXTURE_PRE_LEVELS = REPO / "tests" / "fixtures" / "fx-store-pre-levels.json"
#: Written by the store as it was with five effects and per-effect levels
#: (origin/dev at dee9e03, its own PatchFxStore, 2026-10-13): mixed masks
#: (none, one, two, all five), depths and five-entry levels, both targets.
FIXTURE_FIVE_EFFECTS = REPO / "tests" / "fixtures" / "fx-store-five-effects.json"
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

    def route_file(self) -> dict:
        return json.loads(self.route.read_text())


class PickupTests(unittest.TestCase):
    """F1: the scaling law (`fader_pickup.scaled`), shared with loop_mix."""

    def test_a_move_responds_at_once_in_its_direction_without_a_jump(self) -> None:
        # Knob at 100, value at 20: a small move up raises the value a little.
        up = scaled(100, 103, 20.0)
        self.assertGreater(up, 20.0)
        self.assertAlmostEqual(up, 20 + 107 * 3 / 27)
        self.assertLess(up - 20.0, 103 - 100 + 12)  # nowhere near the knob's 103
        down = scaled(100, 97, 20.0)
        self.assertLess(down, 20.0)
        self.assertAlmostEqual(down, 20 - 20 * 3 / 100)
        self.assertEqual(scaled(100, 100, 20.0), 20.0)

    def test_it_converges_on_the_knob_at_both_ends(self) -> None:
        for prev, ref in ((100, 20.0), (5, 90.0), (64, 64.0), (1, 126.0)):
            with self.subTest(prev=prev, ref=ref):
                self.assertEqual(scaled(prev, 0, ref), 0.0)
                self.assertEqual(scaled(prev, 127, ref), 127.0)

    def test_value_and_knob_meet_as_the_knob_sweeps(self) -> None:
        """Both directions: the gap only closes, and is gone at the end."""
        for start, ref, path in ((100, 20.0, range(100, -1, -5)), (10, 90.0, range(10, 128, 7))):
            with self.subTest(start=start):
                prev, value, gap = start, ref, abs(start - ref)
                for raw in path:
                    value = scaled(prev, raw, value)
                    self.assertLessEqual(abs(raw - value), gap + 1e-9)
                    gap, prev = abs(raw - value), raw
                end = 0 if path.step < 0 else 127
                self.assertEqual(scaled(prev, end, value), float(end))

    def test_follow_anchors_first_then_scales(self) -> None:
        pickup = Pickup()
        self.assertIsNone(pickup.follow("k", 40, 100.0))  # never seen: anchors only
        self.assertAlmostEqual(pickup.follow("k", 50, 100.0), scaled(40, 50, 100.0))
        self.assertEqual(pickup.previous("k"), 50)
        pickup.rearm("k")
        self.assertIsNone(pickup.follow("k", 60, 10.0))
        pickup.rearm()
        self.assertNotIn("k", pickup)

    def test_the_crossing_law_is_gone(self) -> None:
        for name in ("catch", "caught"):
            self.assertFalse(hasattr(Pickup, name), name)
        text = (REPO / "patch_browser" / "fx_policy.py").read_text()
        self.assertNotIn(".catch(", text)

    def test_loop_mix_and_the_knob_share_one_law(self) -> None:
        sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))
        import loop_mix

        self.assertIs(loop_mix.scaled, scaled)
        mix = loop_mix.LoopMix()
        mix._pickup_anchor.first_touch(0, 40)
        mix._pickup_ref[0] = 90.0
        self.assertEqual(mix._scaled_level(0, 20), scaled(40, 20, 90.0))
        text = (REPO / "scripts" / "sooperlooper" / "loop_mix.py").read_text()
        self.assertNotIn("(CC_MAX - ref) * (raw - prev)", text)  # no second copy

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
        fx = PatchFx(mask=D | C, depth=0.25, target=MOD_WHEEL_EFFECT, levels=(1.0, 0.5, 0.25, 0.0, 0.75, 0.6, 0.35))
        self.store.set("Keys", fx, stable_key="factory:Test/Keys")
        again = PatchFxStore(self.store_path)
        self.assertEqual(again.get("Keys", stable_key="factory:Test/Keys"), fx)
        self.assertEqual(
            self.saved(),
            {
                "factory:Test/Keys": {
                    "mask": D | C,
                    "depth": 0.25,
                    "target": "effect",
                    "levels": [1.0, 0.5, 0.25, 0.0, 0.75, 0.6, 0.35],
                }
            },
        )

    def test_never_set_is_empty(self) -> None:
        self.assertEqual(self.store.get("Nope", stable_key="factory:Test/Nope"), PatchFx())

    def test_an_empty_mask_is_a_value_not_a_default(self) -> None:
        """"No FX" chosen for a patch is kept; it is not "use the default"."""
        self.store.set("Keys", PatchFx(mask=0), stable_key="factory:Test/Keys")
        self.assertEqual(self.saved(), {"factory:Test/Keys": {"mask": 0, "depth": 0.0, "levels": ONES}})
        self.assertEqual(PatchFxStore(self.store_path).get("Keys", stable_key="factory:Test/Keys").mask, 0)

    def test_nothing_read_from_the_file_is_trusted(self) -> None:
        too_big = fx_host_protocol.MASK_ALL + 1
        cases = {
            "mask too big": ({"mask": too_big, "depth": 0.5}, PatchFx(mask=0, depth=0.5, levels=ONES_T)),
            "negative mask": ({"mask": -1}, PatchFx()),
            "bool mask": ({"mask": True}, PatchFx()),
            "float mask": ({"mask": 1.0}, PatchFx()),
            "string mask": ({"mask": "3"}, PatchFx()),
            "depth too big": ({"mask": 3, "depth": 2.0}, PatchFx(mask=3, depth=0.0, levels=ONES_T)),
            "nan depth": ({"mask": 3, "depth": float("nan")}, PatchFx(mask=3, depth=0.0, levels=ONES_T)),
            "bool depth": ({"mask": 3, "depth": True}, PatchFx(mask=3, depth=0.0, levels=ONES_T)),
            "bad target": ({"mask": 3, "target": "sideways"}, PatchFx(mask=3, depth=0.0, levels=ONES_T)),
            # Levels: not a list is every level 1.0; a bad entry is 1.0, an
            # out-of-range one is clamped; a short list (an older build) is
            # padded with 1.0 and a long one (a newer build) cut.
            "levels not a list": ({"mask": 3, "levels": {"0": 0.5}}, PatchFx(mask=3, depth=0.0, levels=ONES_T)),
            "bad level entries": (
                {"mask": 3, "levels": ["x", 2.0, -1, True, float("nan")]},
                PatchFx(mask=3, depth=0.0, levels=(1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0)),
            ),
            "short levels": ({"mask": 3, "levels": [0.5, 0.25]}, PatchFx(mask=3, depth=0.0, levels=(0.5, 0.25, 1.0, 1.0, 1.0, 1.0, 1.0))),
            "long levels": (
                {"mask": 3, "levels": [0.5, 0.25, 0.1, 0.2, 0.3, 0.9, 0.8, 0.7]},
                PatchFx(mask=3, depth=0.0, levels=(0.5, 0.25, 0.1, 0.2, 0.3, 0.9, 0.8)),
            ),
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
        self.assertEqual({f.name for f in fields(PatchFx)}, {"mask", "depth", "target", "levels", "bass_hz"})
        for name in ("depth_for", "depths", "effect"):
            self.assertFalse(hasattr(PatchFx(), name), name)
        for name in ("select_effect", "set_global_effect", "effect_id", "effect_name"):
            self.assertFalse(hasattr(FxPolicy, name), name)
        for name in ("EFFECT_PREF_KEY", "DEFAULT_EFFECT", "normalize_effect", "effect_name"):
            self.assertFalse(hasattr(fx_policy, name), name)
        # Round 5 (F3): no global effect state either.
        for name in ("set_global_mask", "global_mask", "_persist_global_mask"):
            self.assertFalse(hasattr(FxPolicy, name), name)
        for name in ("MASK_PREF_KEY", "LEGACY_EFFECT_PREF_KEY", "DEFAULT_MASK", "_pref_mask"):
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
            "Keys/Bonita Keys": PatchFx(mask=D, depth=0.25, levels=ONES_T),
            # Phaser set by the knob: CC 77.
            "Strings/Bowed String": PatchFx(mask=P, depth=0.6062992125984252, levels=ONES_T),
            # Tremolo chosen, never given a depth: Tremolo at the factory 0.
            "Pads/Glass Pad": PatchFx(mask=T, depth=0.0, levels=ONES_T),
            # Only the switch was ever moved: the factory effect state (F3).
            "Leads/Saw Lead": PatchFx(mask=0, depth=0.0, target=MOD_WHEEL_EFFECT, levels=ONES_T),
            # A depth for Tremolo, then Phaser selected: Phaser, never set (0).
            "Bass/Sub Bass": PatchFx(mask=P, depth=0.0, target=MOD_WHEEL_EFFECT, levels=ONES_T),
        }
        for rel, fx in want.items():
            with self.subTest(rel):
                self.assertEqual(store.get(Path(rel).name, stable_key=f"factory:{rel}"), fx)
        # The next save writes every entry in the new shape, not just the touched one.
        store.set("Saw Lead", PatchFx(target=MOD_WHEEL_EFFECT), stable_key="factory:Leads/Saw Lead")
        self.assertEqual(
            self.saved(),
            {
                "factory:Keys/Bonita Keys": {"mask": D, "depth": 0.25, "levels": ONES},
                "factory:Strings/Bowed String": {"mask": P, "depth": 0.6062992125984252, "levels": ONES},
                "factory:Pads/Glass Pad": {"mask": T, "depth": 0.0, "levels": ONES},
                "factory:Leads/Saw Lead": {"mask": 0, "depth": 0.0, "target": "effect", "levels": ONES},
                "factory:Bass/Sub Bass": {"mask": P, "depth": 0.0, "target": "effect", "levels": ONES},
            },
        )

    def test_a_migrated_patch_loads_and_sends_its_old_sound(self) -> None:
        self.store_path.write_bytes(FIXTURE_150.read_bytes())
        policy = self._policy(store=PatchFxStore(self.store_path))
        keys = self._patch("Bonita Keys", WITH_MODWHEEL)
        policy.on_patch_loaded(keys, patch_name="Bonita Keys", stable_key="factory:Keys/Bonita Keys")
        self.now += fx_policy.ROUTE_REFRESH_S
        self.router.refresh()
        self.assertEqual((policy.mask, policy.depth), (D, 0.25))
        self.assertEqual(self.sender.sent, [(D, 0.25)])
        # A migrated patch with only a target: the factory effect state, not
        # what was played last.
        lead = self._patch("Saw Lead", WITH_MODWHEEL)
        policy.on_patch_loaded(lead, patch_name="Saw Lead", stable_key="factory:Leads/Saw Lead")
        self.assertEqual((policy.mask, policy.depth, policy.target), (0, 0.0, MOD_WHEEL_EFFECT))

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

    def test_round4_entries_are_completed_with_the_factory_default(self) -> None:
        """F3 migration on entries shaped like the appliance's store: mask
        only, mask + depth, mask + depth + target. A missing field gets the
        factory value; nothing is borrowed from a global or last-used value,
        even with stale global prefs in the file."""
        (self.root / "ui.json").write_text(json.dumps({"fx_mask": D | T, "fx_effect": TREMOLO}))
        self.store_path.write_bytes(FIXTURE_ROUND4.read_bytes())
        store = PatchFxStore(self.store_path)
        want = {
            "Keys/Mask Only": PatchFx(mask=6, depth=0.0, levels=ONES_T),
            "Pads/Mask And Depth": PatchFx(mask=6, depth=0.5669291338582677, levels=ONES_T),
            "Leads/Mask Depth Target": PatchFx(mask=9, depth=0.25, target=MOD_WHEEL_EFFECT, levels=ONES_T),
            "Bass/No FX Chosen": PatchFx(mask=0, depth=0.0, levels=ONES_T),
        }
        for rel, fx in want.items():
            with self.subTest(rel):
                self.assertEqual(store.get(Path(rel).name, stable_key=f"factory:{rel}"), fx)
        # Loaded, each plays exactly that (the mask-only patch at depth 0).
        policy = self._policy(store=store, global_target=MOD_WHEEL_EFFECT)
        for rel, fx in want.items():
            with self.subTest(load=rel):
                policy.on_patch_loaded(self._patch(Path(rel).name, NO_ROUTINGS), patch_name=Path(rel).name,
                                       stable_key=f"factory:{rel}")
                self.now += fx_policy.ROUTE_REFRESH_S
                self.router.refresh()
                self.assertEqual((policy.mask, policy.depth), (fx.mask, fx.depth))
                self.assertEqual(self.sender.sent[-1], (fx.mask, fx.depth))
        # The next save writes every entry whole.
        store.save()
        self.assertEqual(
            self.saved(),
            {
                "factory:Keys/Mask Only": {"mask": 6, "depth": 0.0, "levels": ONES},
                "factory:Pads/Mask And Depth": {"mask": 6, "depth": 0.5669291338582677, "levels": ONES},
                "factory:Leads/Mask Depth Target": {"mask": 9, "depth": 0.25, "target": "effect", "levels": ONES},
                "factory:Bass/No FX Chosen": {"mask": 0, "depth": 0.0, "levels": ONES},
            },
        )

    def test_stale_global_prefs_are_neither_read_nor_written(self) -> None:
        prefs = self.root / "ui.json"
        prefs.write_text(json.dumps({"fx_effect": DRIVE, "fx_mask": P | T, "theme_mode": "dark"}))
        policy = FxPolicy(enabled=True, route_path=self.route, levels_path=self.levels, store=self.store)
        self.load(self.pad, policy)
        self.assertEqual(policy.mask, 0)
        policy.toggle_effect(CHORUS)
        self.assertEqual(json.loads(prefs.read_text()), {"fx_effect": DRIVE, "fx_mask": P | T, "theme_mode": "dark"})


class LevelMigrationTests(_Rig):
    """Per-effect levels: an entry without them plays as it did, every level 1.0."""

    WANT = {
        "Keys/Bonita Keys": PatchFx(mask=D | C, depth=0.5, target=MOD_WHEEL_EFFECT, levels=ONES_T),
        # Stored as 31: the five effects of its day, never the two added since.
        "Pads/Glass Pad": PatchFx(mask=0b11111, depth=0.8, levels=ONES_T),
        "Leads/Saw Lead": PatchFx(mask=0, depth=0.3, levels=ONES_T),
        "Bass/Sub Bass": PatchFx(mask=P | (1 << FLANGER), depth=1.0, levels=ONES_T),
    }

    def test_the_pre_levels_store_file_migrates(self) -> None:
        raw = json.loads(FIXTURE_PRE_LEVELS.read_text())
        self.assertTrue(raw and all("levels" not in entry for entry in raw.values()))
        self.store_path.write_bytes(FIXTURE_PRE_LEVELS.read_bytes())
        store = PatchFxStore(self.store_path)
        for rel, fx in self.WANT.items():
            with self.subTest(rel):
                self.assertEqual(store.get(Path(rel).name, stable_key=f"factory:{rel}"), fx)
        # The next save writes every entry whole, levels included, and
        # changes nothing else in it.
        store.save()
        self.assertEqual(
            self.saved(),
            {key: dict(entry, levels=ONES) for key, entry in raw.items()},
        )

    def test_a_pre_levels_patch_sends_its_old_sound(self) -> None:
        """Mask and depth as before, every level 1.0 on the wire: `chainl`
        with ones, which the engine plays bit for bit as `chain` (fx-selftest,
        test_level_is_a_depth_product)."""
        self.store_path.write_bytes(FIXTURE_PRE_LEVELS.read_bytes())
        policy = self._policy(store=PatchFxStore(self.store_path))
        wire: list[bytes] = []
        self.router._sender = fx_policy.FxHostSender(send=wire.append, log=None)
        for rel, fx in self.WANT.items():
            with self.subTest(rel):
                policy.on_patch_loaded(self._patch(Path(rel).name, NO_ROUTINGS), patch_name=Path(rel).name,
                                       stable_key=f"factory:{rel}")
                self.now += fx_policy.ROUTE_REFRESH_S
                self.router.refresh()
                self.assertEqual((policy.mask, policy.depth, policy.levels), (fx.mask, fx.depth, ONES_T))
                self.assertEqual(wire[-1], fx_host_protocol.encode_chain_levels(fx.mask, fx.depth, ONES))
                self.assertTrue(wire[-1].endswith(b" 1.000000" * len(fx_host_protocol.EFFECT_IDS)))


class SevenEffectsMigrationTests(_Rig):
    """The rotary (id 5) and the auto-wah (id 6) appended: every setting a
    patch already has means what it meant. Masks keep their bits (31 is the
    five effects of its day, not "all"), depths and targets are untouched, the
    five stored levels are the first five, and the new two read off at 1.0."""

    NEW_BITS = (1 << fx_host_protocol.EFFECT_ROTARY) | (1 << fx_host_protocol.EFFECT_AUTO_WAH)

    def _raw(self) -> dict:
        raw = json.loads(FIXTURE_FIVE_EFFECTS.read_text())
        # What the five-effect build wrote: five levels, masks under 32.
        self.assertTrue(raw)
        for entry in raw.values():
            self.assertEqual(len(entry["levels"]), 5)
            self.assertLess(entry["mask"], 32)
        self.assertIn(0b11111, {e["mask"] for e in raw.values()})
        self.assertIn(0, {e["mask"] for e in raw.values()})
        return raw

    def test_every_stored_value_reads_back_with_its_meaning(self) -> None:
        raw = self._raw()
        self.store_path.write_bytes(FIXTURE_FIVE_EFFECTS.read_bytes())
        store = PatchFxStore(self.store_path)
        for key, entry in raw.items():
            rel = key.split(":", 1)[1]
            with self.subTest(rel):
                fx = store.get(Path(rel).name, stable_key=key)
                self.assertEqual(fx.mask, entry["mask"])
                self.assertEqual(fx.mask & self.NEW_BITS, 0)
                self.assertEqual(fx.depth, entry["depth"])
                self.assertEqual(fx.target, entry.get("target"))
                self.assertEqual(fx.levels[:5], tuple(entry["levels"]))
                self.assertEqual(fx.levels[5:], (1.0, 1.0))

    def test_the_next_save_only_appends_the_two_new_levels(self) -> None:
        raw = self._raw()
        self.store_path.write_bytes(FIXTURE_FIVE_EFFECTS.read_bytes())
        PatchFxStore(self.store_path).save()
        self.assertEqual(
            self.saved(),
            {key: dict(entry, levels=entry["levels"] + [1.0, 1.0]) for key, entry in raw.items()},
        )
        # And that file reads back to the same values again: a fixed point.
        again = PatchFxStore(self.store_path)
        again.save()
        self.assertEqual(self.saved(), {key: dict(e, levels=e["levels"] + [1.0, 1.0]) for key, e in raw.items()})

    def test_each_patch_sends_what_the_five_effect_build_sent(self) -> None:
        """On the wire: the five-effect datagram (mask, depth, five levels)
        plus two levels of 1.0 for effects the mask leaves off. The client's
        own parser reads the old datagram and the new one to the same state
        (it pads a short level list with 1.0)."""
        raw = self._raw()
        self.store_path.write_bytes(FIXTURE_FIVE_EFFECTS.read_bytes())
        policy = self._policy(store=PatchFxStore(self.store_path))
        wire: list[bytes] = []
        self.router._sender = fx_policy.FxHostSender(send=wire.append, log=None)
        old_wires, new_wires = [], []
        for key, entry in raw.items():
            rel = key.split(":", 1)[1]
            with self.subTest(rel):
                policy.on_patch_loaded(self._patch(Path(rel).name, NO_ROUTINGS), patch_name=Path(rel).name,
                                       stable_key=key)
                self.now += fx_policy.ROUTE_REFRESH_S
                self.router.refresh()
                old = "chainl {} {:.6f} {}".format(
                    entry["mask"], entry["depth"], " ".join(f"{v:.6f}" for v in entry["levels"])
                ).encode()
                self.assertEqual(wire[-1], old + b" 1.000000 1.000000")
                old_wires.append(old.decode())
                new_wires.append(wire[-1].decode())
        from tests.test_fx_host import _compiler, _parse_lines, _selftest_binary

        if _compiler() is None:
            self.skipTest("no C compiler: the client's parser is not run here (test_fx_host covers it)")
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertEqual(_parse_lines(binary, old_wires), _parse_lines(binary, new_wires))


class LevelMemoryTests(_Rig):
    """Each patch remembers its levels; a level moves only itself."""

    def test_each_patch_recalls_its_levels(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C | D | P)
        self.policy.set_depth(0.6, final=True)
        self.policy.set_level(DRIVE, 0.25, final=True)
        self.policy.set_level(PHASER, 0.5, final=True)
        self.load(self.pad)
        self.settle()
        # Another patch: the factory levels, never the last-used ones.
        self.assertEqual(self.policy.levels, ONES_T)
        self.assertEqual(self.sender.levels[-1], ONES_T)
        self.policy.set_mask(C)
        self.policy.set_level(CHORUS, 0.7, final=True)
        self.load(self.keys)
        self.settle()
        keys_levels = (1.0, 0.25, 0.5, 1.0, 1.0, 1.0, 1.0)
        self.assertEqual(self.policy.levels, keys_levels)
        self.assertEqual((self.sender.sent[-1], self.sender.levels[-1]), ((C | D | P, 0.6), keys_levels))
        self.assertEqual(self.route_file()["levels"], list(keys_levels))
        # A fresh UI process reads them back from the file.
        self.policy.flush_store()
        self.assertEqual(self.saved()["factory:Test/Keys"]["levels"], list(keys_levels))
        self.assertEqual(self.saved()["factory:Test/Pad"]["levels"], [0.7, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        fresh = self._policy(store=PatchFxStore(self.store_path))
        self.load(self.keys, fresh)
        self.assertEqual(fresh.levels, keys_levels)
        self.load(self.pad, fresh)
        self.assertEqual(fresh.levels, (0.7, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0))

    def test_a_level_moves_nothing_else(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C | D)
        self.policy.set_depth(0.4, final=True)
        self.settle()
        before = self.route_file()
        self.policy.set_level(DRIVE, 0.3, final=True)
        after = self.route_file()
        self.assertEqual(after["levels"], [1.0, 0.3, 1.0, 1.0, 1.0, 1.0, 1.0])
        for key in ("cc1", "mask", "target", "route_stamp", "depth_request", "depth_stamp"):
            self.assertEqual(after[key], before[key], key)
        self.settle()
        self.assertEqual((self.policy.mask, self.policy.depth), (C | D, 0.4))
        self.assertEqual((self.sender.sent[-1], self.sender.levels[-1]), ((C | D, 0.4), (1.0, 0.3, 1.0, 1.0, 1.0, 1.0, 1.0)))

    def test_an_unticked_effect_keeps_its_level_and_takes_no_new_one(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C | T)
        self.policy.set_level(TREMOLO, 0.35, final=True)
        self.policy.toggle_effect(TREMOLO)  # off: greyed, keeps 0.35
        self.assertEqual(self.policy.level(TREMOLO), 0.35)
        self.policy.set_level(TREMOLO, 0.9, final=True)  # inert while off
        self.assertEqual(self.policy.level(TREMOLO), 0.35)
        self.assertEqual(self.route_file()["levels"][TREMOLO], 0.35)
        self.policy.toggle_effect(TREMOLO)  # back on at the level it kept
        self.settle()
        self.assertEqual(self.sender.levels[-1][TREMOLO], 0.35)

    def test_a_drag_writes_at_most_every_publish_interval_and_lands_the_last(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C)
        writes = []
        real = fx_policy.atomic_write_json

        def spy(path, data):
            if Path(path) == self.route:
                writes.append(data["levels"][CHORUS])
            return real(path, data)

        with mock.patch.object(fx_policy, "atomic_write_json", side_effect=spy):
            for i, level in enumerate((0.9, 0.8, 0.7, 0.6, 0.5)):
                self.now += 0.01
                self.policy.set_level(CHORUS, level)
            self.assertEqual(writes, [0.9])  # one write inside DEPTH_PUBLISH_S
            self.now += fx_policy.DEPTH_PUBLISH_S
            self.policy.set_level(CHORUS, 0.45)
            self.assertEqual(writes, [0.9, 0.45])
            self.policy.set_level(CHORUS, 0.4)  # throttled...
            self.assertEqual(writes, [0.9, 0.45])
            self.policy.finish_level()  # ...and released: the last one lands
            self.assertEqual(writes, [0.9, 0.45, 0.4])
            self.policy.finish_level()  # nothing pending: no write
            self.assertEqual(writes, [0.9, 0.45, 0.4])
        self.assertFalse(self.policy.dragging_level)

    def test_a_final_level_is_written_at_once_even_inside_the_interval(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C)
        self.policy.set_level(CHORUS, 0.9)
        self.now += 0.01
        self.policy.set_level(CHORUS, 0.3, final=True)
        self.assertEqual(self.route_file()["levels"][CHORUS], 0.3)
        self.assertFalse(self.policy.dragging_level)

    def test_a_route_file_without_levels_plays_every_level_at_one(self) -> None:
        """A UI from before per-effect levels (the UI and the remapper restart
        on their own): its route file has no `levels`, and the remapper sends
        every level 1.0, which is the sound that UI chose."""
        self.route.write_text(json.dumps({"cc1": "fx", "mask": C | D, "route_stamp": "r1",
                                          "depth_request": 0.5, "depth_stamp": "d1"}))
        self.now += fx_policy.ROUTE_REFRESH_S
        self.router.refresh()
        self.assertEqual((self.sender.sent[-1], self.sender.levels[-1]), ((C | D, 0.5), ONES_T))

    def test_levels_reach_the_fx_host_live_while_dragging(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(P)
        self.policy.set_depth(0.8, final=True)
        self.settle()
        for level in (0.9, 0.7, 0.5):
            self.now += fx_policy.DEPTH_PUBLISH_S
            self.policy.set_level(PHASER, level)
            self.now += fx_policy.ROUTE_REFRESH_S
            self.router.refresh()
            self.assertEqual(self.sender.levels[-1][PHASER], level)
            self.assertEqual(self.sender.sent[-1], (P, 0.8))

    def test_a_restarted_remapper_reasserts_the_levels(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C | D)
        self.policy.set_depth(0.5, final=True)
        self.policy.set_level(CHORUS, 0.2, final=True)
        self.settle()
        restarted = _RecordingSender()
        fresh = Cc1Router(sender=restarted, route_path=self.route, levels_path=self.root / "other-levels.json",
                          clock=lambda: self.now)
        self.now += fx_policy.ROUTE_REFRESH_S
        fresh.refresh()
        self.assertEqual(restarted.sent, [(C | D, 0.5)])
        self.assertEqual(restarted.levels, [(0.2, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])

    def test_bad_level_calls_change_nothing(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C)
        before = self.policy.levels
        for effect_id, level in ((CHORUS, float("nan")), (CHORUS, float("inf")), (9, 0.5), (True, 0.5), (-1, 0.5)):
            with self.subTest(effect=effect_id, level=level):
                self.policy.set_level(effect_id, level, final=True)
                self.assertEqual(self.policy.levels, before)
        self.policy.set_level(CHORUS, 7.0, final=True)  # clamped, as a slider cannot exceed 100 %
        self.assertEqual(self.policy.level(CHORUS), 1.0)
        self.policy.set_level(CHORUS, -3.0, final=True)
        self.assertEqual(self.policy.level(CHORUS), 0.0)

    def test_off_writes_and_sends_nothing(self) -> None:
        policy = self._policy(enabled=False)
        self.load(self.keys, policy)
        policy.set_level(CHORUS, 0.3, final=True)
        self.assertFalse(self.route.exists())
        self.assertEqual(policy.levels, ONES_T)


class BassBypassMemoryTests(_Rig):
    """The bass bypass's crossover (#173): stored per patch next to the mask,
    depth and levels; unset (every entry written before it) is the 120 Hz
    default; the remapper sends it."""

    DEFAULT = fx_host_protocol.BASS_DEFAULT_HZ

    def test_an_entry_from_before_reads_unset_and_plays_the_default(self) -> None:
        old = {"factory:Test/Keys": {"mask": C | D, "depth": 0.4, "levels": [1.0, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0],
                                     "target": "effect"}}
        self.store_path.write_text(json.dumps(old))
        store = PatchFxStore(self.store_path)
        fx = store.get("Keys", stable_key="factory:Test/Keys")
        self.assertIsNone(fx.bass_hz)
        self.assertEqual((fx.mask, fx.depth, fx.target), (C | D, 0.4, "effect"))
        policy = self._policy(store=store)
        self.load(self.keys, policy)
        self.assertEqual(policy.bass_hz, self.DEFAULT)
        self.assertEqual(self.route_file()["bass_hz"], self.DEFAULT)
        # Saved again, the entry is what it was: every value kept, no bass_hz
        # written, so it keeps following the default.
        store.save()
        self.assertEqual(self.saved(), old)

    def test_round_trip_and_off(self) -> None:
        for hz in (fx_host_protocol.BASS_OFF, 80.0, 175.0, 300.0):
            with self.subTest(hz=hz):
                self.store.set("Keys", PatchFx(mask=C, bass_hz=hz), stable_key="k")
                self.assertEqual(self.saved()["k"]["bass_hz"], hz)
                self.assertEqual(PatchFxStore(self.store_path).get("Keys", stable_key="k").bass_hz, hz)

    def test_nothing_read_from_the_file_is_trusted(self) -> None:
        for bad in (79.9, 300.1, -1, float("inf"), "120", True, None, [120]):
            with self.subTest(bad=bad):
                raw = json.dumps({"k": {"mask": C, "depth": 0.5, "bass_hz": bad}}) if bad != float("inf") else \
                    '{"k": {"mask": 1, "depth": 0.5, "bass_hz": Infinity}}'
                self.store_path.write_text(raw)
                fx = PatchFxStore(self.store_path).get("Keys", stable_key="k")
                self.assertIsNone(fx.bass_hz)
                self.assertEqual(fx_policy.resolve_config(global_target=MOD_WHEEL_PATCH_FIRST, patch_config=fx).bass_hz,
                                 self.DEFAULT)

    def test_an_entry_with_only_a_crossover_is_kept_and_completed(self) -> None:
        self.store_path.write_text(json.dumps({"k": {"bass_hz": 150.0}}))
        fx = PatchFxStore(self.store_path).get("Keys", stable_key="k")
        self.assertEqual((fx.mask, fx.depth, fx.levels, fx.bass_hz), (0, 0.0, ONES_T, 150.0))

    def test_each_patch_recalls_its_crossover_and_a_new_one_gets_the_default(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C)
        self.policy.set_depth(0.6, final=True)
        self.policy.set_bass(200.0, final=True)
        self.load(self.pad)
        self.assertEqual(self.policy.bass_hz, self.DEFAULT)
        self.policy.set_mask(P)
        self.policy.set_bass(fx_host_protocol.BASS_OFF, final=True)
        self.load(self.keys)
        self.settle()
        self.assertEqual(self.policy.bass_hz, 200.0)
        self.assertEqual(self.sender.bass[-1], 200.0)
        self.policy.flush_store()
        self.assertEqual(self.saved()["factory:Test/Keys"]["bass_hz"], 200.0)
        self.assertEqual(self.saved()["factory:Test/Pad"]["bass_hz"], 0.0)
        fresh = self._policy(store=PatchFxStore(self.store_path))
        self.load(self.pad, fresh)
        self.assertEqual(fresh.bass_hz, fx_host_protocol.BASS_OFF)

    def test_other_changes_leave_an_unset_crossover_unset(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C | D)
        self.policy.set_depth(0.3, final=True)
        self.policy.set_level(DRIVE, 0.5, final=True)
        self.policy.flush_store()
        self.assertNotIn("bass_hz", self.saved()["factory:Test/Keys"])

    def test_the_crossover_moves_nothing_else(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C | D)
        self.policy.set_depth(0.4, final=True)
        self.policy.set_level(DRIVE, 0.3, final=True)
        self.settle()
        before = self.route_file()
        self.policy.set_bass(90.0, final=True)
        after = self.route_file()
        self.assertEqual(after["bass_hz"], 90.0)
        for key in ("cc1", "mask", "levels", "target", "route_stamp", "depth_request", "depth_stamp"):
            self.assertEqual(after[key], before[key], key)
        self.settle()
        self.assertEqual((self.sender.sent[-1], self.sender.bass[-1]), ((C | D, 0.4), 90.0))

    def test_with_no_effect_ticked_it_is_locked_and_moves_nothing(self) -> None:
        self.load(self.keys)
        self.assertTrue(self.policy.bass_locked)
        self.policy.set_bass(250.0, final=True)
        self.assertEqual(self.policy.bass_hz, self.DEFAULT)
        self.policy.set_mask(T)
        self.assertFalse(self.policy.bass_locked)
        self.policy.set_bass(250.0, final=True)
        self.assertEqual(self.policy.bass_hz, 250.0)

    def test_bad_values_change_nothing(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C)
        for bad in (float("nan"), 50.0, 400.0, -1.0, True, "120"):
            with self.subTest(bad=bad):
                self.policy.set_bass(bad, final=True)
                self.assertEqual(self.policy.bass_hz, self.DEFAULT)

    def test_a_drag_is_throttled_and_the_release_lands(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(C)
        writes = []
        real = fx_policy.atomic_write_json

        def spy(path, data):
            if Path(path) == self.route:
                writes.append(data["bass_hz"])
            return real(path, data)

        with mock.patch.object(fx_policy, "atomic_write_json", side_effect=spy):
            for hz in (130.0, 140.0, 150.0):
                self.now += 0.01
                self.policy.set_bass(hz)
            self.assertEqual(writes, [130.0])
            self.assertTrue(self.policy.dragging_bass)
            self.policy.finish_bass()
            self.assertEqual(writes, [130.0, 150.0])
            self.policy.finish_bass()
            self.assertEqual(writes, [130.0, 150.0])
        self.assertFalse(self.policy.dragging_bass)

    def test_the_remapper_sends_it_live_and_a_restart_reasserts_it(self) -> None:
        self.load(self.keys)
        self.policy.set_mask(P)
        self.policy.set_depth(0.8, final=True)
        self.settle()
        for hz in (100.0, 175.0, 0.0):
            self.now += fx_policy.DEPTH_PUBLISH_S
            self.policy.set_bass(hz)
            self.now += fx_policy.ROUTE_REFRESH_S
            self.router.refresh()
            self.assertEqual((self.sender.sent[-1], self.sender.bass[-1]), ((P, 0.8), hz))
        restarted = _RecordingSender()
        fresh = Cc1Router(sender=restarted, route_path=self.route, levels_path=self.root / "other-levels.json",
                          clock=lambda: self.now)
        self.now += fx_policy.ROUTE_REFRESH_S
        fresh.refresh()
        self.assertEqual(restarted.bass, [0.0])

    def test_on_the_wire_the_crossover_goes_first_and_rides_every_chain(self) -> None:
        wire: list[bytes] = []
        self.router._sender = fx_policy.FxHostSender(send=wire.append, log=None)
        self.load(self.keys)
        self.policy.set_mask(C)
        self.policy.set_depth(0.5, final=True)
        self.settle()
        self.assertEqual(wire[0], b"bass 120.000000")
        self.assertTrue(wire[1].startswith(b"chainl 1 0.500000"))
        self.policy.set_bass(80.0, final=True)
        self.settle()
        self.assertEqual(wire[2:], [b"bass 80.000000"])  # the chain did not change: not resent
        self.policy.set_depth(0.6, final=True)
        self.settle()
        # The crossover did not change, but it goes ahead of every chain sent,
        # so a restarted fx host (which starts Off) relearns it (#185 review).
        self.assertEqual(wire[3], b"bass 80.000000")
        self.assertTrue(wire[4].startswith(b"chainl 1 0.600000"))
        self.assertEqual(len(wire), 5)

    def test_off_writes_and_sends_nothing(self) -> None:
        policy = self._policy(enabled=False)
        self.load(self.keys, policy)
        policy.set_bass(200.0, final=True)
        self.assertFalse(self.route.exists())


class KeyStabilityTests(_Rig):
    """The existing sidecar key logic, unchanged: stable key, stem fallback, migration."""

    def _p(self, rel: str) -> dict:
        return {"name": Path(rel).stem, "path": f"{ROOT}/{rel}", "stable_key": f"factory:{rel[:-4]}"}

    def test_legacy_stem_entry_is_read_and_rewritten_under_the_stable_key(self) -> None:
        self.store_path.write_text(json.dumps({"Keys": {"mask": D}}))
        store = PatchFxStore(self.store_path)
        self.assertEqual(store.get("Keys", stable_key="factory:Keys/Keys").mask, D)
        store.set("Keys", PatchFx(mask=P), stable_key="factory:Keys/Keys")
        self.assertEqual(json.loads(self.store_path.read_text()), {"factory:Keys/Keys": {"mask": P, "depth": 0.0, "levels": ONES}})

    def test_post_scan_migration_moves_unambiguous_stems(self) -> None:
        # #150-shaped stem entries: migrated in shape and in key.
        self.store_path.write_text(json.dumps({"Keys": {"effect": DRIVE}, "Lead": {"effect": PHASER}}))
        store = PatchFxStore(self.store_path)
        patches = [self._p("Keys/Keys.fxp"), self._p("Bass/Lead.fxp"), self._p("Pad/Lead.fxp")]
        warnings = migrate_sidecar_stores(fx=store, patches=patches, patch_dirs=[Path(ROOT)])
        saved = json.loads(self.store_path.read_text())
        self.assertEqual(saved["factory:Keys/Keys"], {"mask": D, "depth": 0.0, "levels": ONES})
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
    def test_never_set_patch_gets_the_factory_default(self) -> None:
        """F3: no effect at depth 0, whatever was played before."""
        self.load(self.keys)
        self.policy.set_mask(P | T)
        self.policy.set_depth(0.8, final=True)
        self.load(self.pad)
        self.settle()
        self.assertEqual((self.policy.mask, self.policy.depth), (0, 0.0))
        self.assertEqual(self.sender.sent[-1], (0, 0.0))
        self.policy.flush_store()
        self.assertNotIn("factory:Test/Pad", self.saved())  # loading writes nothing

    def test_each_patch_remembers_mask_depth_and_target(self) -> None:
        self.load(self.keys)
        self.policy.toggle_effect(DRIVE)
        self.policy.set_depth(0.5, final=True)
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.load(self.pad)
        self.policy.toggle_effect(CHORUS)  # from the factory default, not from Keys
        self.policy.toggle_effect(TREMOLO)
        self.policy.set_depth(0.2, final=True)
        self.load(self.keys)
        self.settle()
        self.assertEqual(self.policy.mask, D)
        self.assertEqual(self.policy.depth, 0.5)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(self.sender.sent[-1], (D, 0.5))
        # A fresh UI process reads the same back from the file.
        self.policy.flush_store()
        fresh = self._policy(store=PatchFxStore(self.store_path))
        self.load(self.pad, fresh)
        self.assertEqual((fresh.mask, fresh.depth, fresh.target), (C | T, 0.2, MOD_WHEEL_PATCH_FIRST))
        self.load(self.keys, fresh)
        self.assertEqual((fresh.mask, fresh.depth, fresh.target), (D, 0.5, MOD_WHEEL_EFFECT))
        self.assertEqual(
            self.saved(),
            {
                "factory:Test/Keys": {"mask": D, "depth": 0.5, "target": "effect", "levels": ONES},
                "factory:Test/Pad": {"mask": C | T, "depth": 0.2, "levels": ONES},
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
        stamp = self.route_file()["route_stamp"]
        steps = ((DRIVE, D), (PHASER, D | P), (FLANGER, D | P | (1 << FLANGER)), (CHORUS, C | D | P | (1 << FLANGER)),
                 (DRIVE, C | P | (1 << FLANGER)), (FLANGER, C | P), (PHASER, C))
        for effect, want in steps:
            with self.subTest(effect=effect):
                self.policy.toggle_effect(effect)
                self.settle()
                self.assertEqual(self.policy.mask, want)
                self.assertEqual(self.policy.depth, 0.3)
                self.assertEqual(self.router.depth, 0.3)
                self.assertEqual(self.sender.sent[-1], (want, 0.3))
                route = self.route_file()
                self.assertEqual((route["mask"], route["route_stamp"]), (want, stamp))
        self.policy.flush_store()
        self.assertEqual(self.saved()["factory:Test/Keys"], {"mask": C, "depth": 0.3, "levels": ONES})

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
                self.assertEqual(self.policy.mask, 0)

    def test_a_toggle_never_reaches_another_patch(self) -> None:
        """F3: the leak. A pick on one patch is not what the next one plays."""
        self.load(self.keys)
        self.policy.toggle_effect(TREMOLO)
        self.policy.set_depth(0.7, final=True)
        self.load(self.pad)
        self.assertEqual((self.policy.mask, self.policy.depth), (0, 0.0))
        prefs = self.root / "ui.json"
        self.assertNotIn("fx_mask", json.loads(prefs.read_text()) if prefs.exists() else {})

    def test_a_patch_entry_is_always_written_whole(self) -> None:
        """A depth, a mask or a target alone still writes mask and depth."""
        for change, want in (
            (lambda: self.policy.set_depth(0.6, final=True), {"mask": 0, "depth": 0.6, "levels": ONES}),
            (lambda: self.policy.toggle_effect(DRIVE), {"mask": D, "depth": 0.0, "levels": ONES}),
            (lambda: self.policy.set_target(MOD_WHEEL_EFFECT), {"mask": 0, "depth": 0.0, "target": "effect", "levels": ONES}),
        ):
            with self.subTest(want=want):
                self.store_path.unlink(missing_ok=True)
                self.store.load()
                self.load(self.keys)
                change()
                self.policy.flush_store()
                self.assertEqual(self.saved(), {"factory:Test/Keys": want})


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
        self.assertEqual(self.saved()["factory:Test/Keys"], {"mask": 0, "depth": 0.7, "levels": ONES})

    def test_a_pending_change_is_written_before_the_next_load(self) -> None:
        self.load(self.keys)
        self.policy.set_depth(0.4, final=True)
        self.load(self.pad)
        self.assertEqual(self.saved()["factory:Test/Keys"], {"mask": 0, "depth": 0.4, "levels": ONES})

    def test_shutdown_flush_writes_what_is_pending(self) -> None:
        self.load(self.keys)
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.assertEqual(self.saved(), {})
        self.assertTrue(self.policy.flush_store())
        self.assertEqual(self.saved()["factory:Test/Keys"], {"mask": 0, "depth": 0.0, "target": "effect", "levels": ONES})
        self.assertFalse(self.policy.flush_store())

    def test_off_writes_nothing(self) -> None:
        policy = self._policy(enabled=False)
        self.load(self.keys, policy)
        policy.set_depth(0.5, final=True)
        policy.set_target(MOD_WHEEL_EFFECT)
        policy.toggle_effect(DRIVE)
        policy.set_mask(D)
        self.assertEqual(policy.mask, 0)
        policy.flush_store()
        self.assertFalse(self.store_path.exists())


class PickupInTheRouterTests(_Rig):
    """F1: the knob scales from the value in force; it never waits, never jumps."""

    def test_effect_knob_after_a_patch_switch_responds_at_once_both_directions(self) -> None:
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.policy.set_mask(C)
        self.policy.set_depth(0.2, final=True)  # 25.4 in CC units
        self.load(self.pad)
        self.policy.set_mask(C)
        self.settle()
        self.knob(0)  # the first message this remapper sees: an anchor, nothing else
        self.assertEqual(self.sender.sent[-1], (C, 0.0))
        self.knob(64)  # from depth 0 at knob 0: all the way to the knob
        self.assertAlmostEqual(self.policy.depth, 64 / 127)
        self.load(self.keys)  # depth 0.2 stored; the knob stays at 64
        self.settle()
        sent = len(self.sender.sent)
        self.knob(70)  # the first move after the switch moves the depth, up, a little
        self.assertEqual(len(self.sender.sent), sent + 1)
        up = (25.4 + (127 - 25.4) * 6 / 63) / 127
        self.assertAlmostEqual(self.sender.sent[-1][1], up)
        self.assertLess(self.sender.sent[-1][1], 70 / 127 - 0.2)  # no jump to the knob
        self.knob(35)  # down
        down = (up * 127 - up * 127 * 35 / 70) / 127
        self.assertAlmostEqual(self.sender.sent[-1][1], down)
        self.knob(0)  # bottom of travel: the effect's bottom
        self.assertEqual(self.sender.sent[-1], (C, 0.0))
        self.knob(127)  # and the top
        self.assertEqual(self.sender.sent[-1], (C, 1.0))
        self.assertEqual(self.policy.depth, 1.0)

    def test_patch_mod_knob_forwards_the_scaled_value(self) -> None:
        self.load(self.keys)  # routes the wheel: CC 1 to Surge
        self.policy.set_patch_mod(80)
        self.settle()
        self.assertTrue(self.router.consume([0xB0, 1, 79]))  # first sight: anchors, swallowed
        msg = [0xB0, 1, 70]
        self.assertFalse(self.router.consume(msg))  # forwarded at once...
        self.assertEqual(msg[2], round(80 - 80 * 9 / 79))  # ...scaled from 80, not 70
        self.policy.set_patch_mod(20)  # the fader moved it from under the knob
        self.settle()
        msg = [0xB0, 1, 60]
        self.assertFalse(self.router.consume(msg))
        self.assertEqual(msg[2], round(20 - 20 * 10 / 70))
        self.assertFalse(self.router.consume([0xB0, 1, 0]))
        self.settle()
        self.assertEqual(self.policy.patch_mod, 0)
        msg = [0xB0, 1, 127]
        self.assertFalse(self.router.consume(msg))
        self.assertEqual(msg[2], 127)

    def test_slow_moves_accumulate_instead_of_rounding_away(self) -> None:
        """Surge gets whole CCs; the scaled value is kept unrounded between them."""
        self.load(self.keys)
        self.policy.set_patch_mod(10)
        self.settle()
        self.router.consume([0xB0, 1, 120])  # anchor
        for raw in range(119, 99, -1):  # one step at a time
            self.router.consume([0xB0, 1, raw])
        self.assertEqual(self.router.patch_mod, round(10 * 100 / 120))

    def test_a_fader_move_rearms_it(self) -> None:
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.policy.set_mask(C)
        self.settle()
        self.knob(0, 50)
        self.assertAlmostEqual(self.policy.depth, 50 / 127)
        self.policy.set_depth(0.9, final=True)
        self.settle()
        self.knob(52)  # scales from the fader's 0.9, not from the knob's 52
        self.assertAlmostEqual(self.policy.depth, (0.9 * 127 + (127 - 0.9 * 127) * 2 / 77) / 127)
        self.knob(127)
        self.assertEqual(self.sender.sent[-1], (C, 1.0))

    def test_a_toggle_does_not_rearm_it(self) -> None:
        """Toggling moves no depth: the knob carries on from the depth in force."""
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.policy.set_mask(C)
        self.settle()
        self.knob(0, 50)
        self.policy.toggle_effect(DRIVE)
        self.settle()
        self.assertEqual(self.sender.sent[-1], (C | D, 50 / 127))
        self.knob(60)
        self.assertEqual(self.sender.sent[-1], (C | D, 60 / 127))

    def test_no_effect_ticked_the_knob_moves_nothing(self) -> None:
        """With the empty mask the knob, routed to the effect, is taken from
        Surge and moves nothing: not the depth, not the store, nothing sent.
        Its position is still tracked, so an effect ticked later plays at the
        stored depth and the knob's next move scales from it, no jump.
        (Replaces #142 round 5 E3, where it moved the depth inaudibly.)"""
        self.policy.set_global_default(MOD_WHEEL_EFFECT)
        self.load(self.keys)
        self.policy.toggle_effect(PHASER)
        self.policy.set_depth(0.3, final=True)
        self.policy.toggle_effect(PHASER)  # off again: the empty mask, depth 0.3 stored
        self.settle()
        self.assertEqual(self.policy.mask, 0)
        self.assertEqual(self.sender.sent[-1], (0, 0.3))
        sent = len(self.sender.sent)
        self.knob(40)  # anchors
        self.assertTrue(self.router.consume([0xB0, 1, 0]))  # taken from Surge...
        self.assertTrue(self.router.consume([0xB0, 1, 110]))
        self.settle()
        self.assertEqual(len(self.sender.sent), sent)  # ...and nothing sent
        self.assertAlmostEqual(self.router.depth, 0.3)
        self.assertAlmostEqual(self.policy.depth, 0.3)
        self.policy.flush_store()
        self.assertAlmostEqual(self.saved()["factory:Test/Keys"]["depth"], 0.3)
        self.policy.toggle_effect(PHASER)
        self.settle()
        self.assertEqual(self.sender.sent[-1], (P, 0.3))  # back at the stored depth
        self.knob(120)  # tracked at 110: a small move up from 0.3, not a jump to 120
        expected = (0.3 * 127 + (127 - 0.3 * 127) * 10 / 17) / 127
        self.assertAlmostEqual(self.sender.sent[-1][1], expected)

    def test_faders_show_the_stored_values_not_the_knob(self) -> None:
        self.load(self.keys)
        self.policy.toggle_effect(PHASER)
        self.policy.set_depth(0.25, final=True)
        self.policy.set_patch_mod(40)
        self.load(self.pad)
        self.settle()
        self.knob(0, 127)
        self.load(self.keys)
        self.settle()
        self.assertEqual(self.policy.mask, P)
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
        self.knob(0)
        self.policy.set_patch_mod(90, final=True)
        self.settle()  # knob parked at 0, far from everything
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

    def test_knob_depth_survives_a_flip_away_and_back(self) -> None:
        """F2, the captured sequence: a knob-driven depth, flip away, flip back.

        As on the appliance: the Effect fader last moved to 0, so the route
        file's request is 0.0 under an old stamp; the knob then took the
        depth to 0.567 (CC 72). Flipping away and back must leave 0.567 in
        force, in the UI, in the route file, and for a remapper that reads the
        route file fresh (restarted).
        """
        self.load(self.keys)  # wheel routed: Surge
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.policy.toggle_effect(DRIVE)
        self.policy.toggle_effect(PHASER)  # mask 6, as in the capture
        self.policy.set_depth(0.3, final=False)
        self.policy.set_depth(0.0, final=True)  # the fader last moved to 0
        self.settle()
        self.knob(0, 30, 50, 72)
        depth = 72 / 127
        self.assertAlmostEqual(self.policy.depth, depth)
        self.assertEqual(self.sender.sent[-1], (D | P, depth))
        sent = len(self.sender.sent)
        self.policy.set_target(MOD_WHEEL_PATCH_FIRST)  # flip away
        self.settle()
        self.knob(80, 101)  # the knob plays Surge meanwhile
        self.policy.set_target(MOD_WHEEL_EFFECT)  # flip back
        self.settle(0.5)
        self.assertEqual(len(self.sender.sent), sent)  # nothing sent: no zero
        self.assertAlmostEqual(self.router.depth, depth)
        self.assertAlmostEqual(self.policy.depth, depth)
        self.assertAlmostEqual(self.route_file()["depth_request"], depth)
        restarted = []
        fresh = Cc1Router(sender=mock.Mock(send=lambda m, d, _levels=None, _bass=None: restarted.append((m, d))), route_path=self.route,
                          levels_path=self.root / "other-levels.json", clock=lambda: self.now)
        fresh.refresh(self.now)
        self.assertEqual(len(restarted), 1)
        self.assertAlmostEqual(restarted[0][1], depth)  # not the stale 0.0
        self.policy.flush_store()
        self.assertAlmostEqual(self.saved()["factory:Test/Keys"]["depth"], depth)
        # The first knob move after the flip (the knob is at 101) responds at
        # once, up, from the depth: no jump to the knob.
        self.knob(110)
        self.assertAlmostEqual(self.sender.sent[-1][1], (72 + 55 * 9 / 26) / 127)
        self.assertLess(self.sender.sent[-1][1], 110 / 127)

    def test_the_captured_zero_was_a_patch_load(self) -> None:
        """What the capture at t+293.199 actually was: a new depth stamp and a
        Patch mod resend, which only `on_patch_loaded` + `_resend_patch_mod`
        produce, not a flip. The patch it loaded had no depth of its own; it
        now plays the factory default, and nothing of the patch before."""
        self.load(self.keys)
        self.policy.set_target(MOD_WHEEL_EFFECT)
        self.policy.set_mask(D | P)
        self.settle()
        self.knob(0, 72)
        self.policy.set_target(MOD_WHEEL_PATCH_FIRST)
        self.settle()
        before = self.route_file()
        self.load(self.pad)  # what the tap at t+293 did
        after = self.route_file()
        self.assertNotEqual(after["depth_stamp"], before["depth_stamp"])
        self.settle()
        self.assertEqual(self.sender.sent[-1], (0, 0.0))  # the pad's own (factory) state
        self.load(self.keys)
        self.settle()
        self.assertEqual(self.sender.sent[-1][0], D | P)
        self.assertAlmostEqual(self.sender.sent[-1][1], 72 / 127)

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

    def test_no_button_today_the_row_is_the_choice_and_the_level(self) -> None:
        from patch_browser.geometry import Rect
        from patch_browser.touch_browser_fx_effect_modal import (
            FX_EFFECT_CHOICE_W,
            FX_EFFECT_ROW_SETTINGS_W,
            split_effect_row,
        )

        row = Rect(10, 20, 560, 52)
        self.assertEqual(FX_EFFECT_ROW_SETTINGS_W, 0)
        choice, level, settings = split_effect_row(row)
        self.assertIsNone(settings)
        self.assertEqual((choice.x, choice.w), (row.x, FX_EFFECT_CHOICE_W))
        self.assertEqual(level.right, row.right)
        self.assertLess(choice.right, level.x)
        self.assertEqual((choice.y, choice.h, level.y, level.h), (row.y, row.h, row.y, row.h))

    def test_a_settings_width_splits_the_row(self) -> None:
        from patch_browser.geometry import Rect
        from patch_browser.touch_browser_fx_effect_modal import split_effect_row

        row = Rect(10, 20, 560, 52)
        choice, level, settings = split_effect_row(row, 48)
        self.assertEqual(settings.w, 48)
        self.assertEqual(settings.right, row.right)
        self.assertLess(choice.right, level.x)
        self.assertLess(level.right, settings.x)
        self.assertEqual((choice.y, choice.h, settings.y, settings.h), (row.y, row.h, row.y, row.h))

if __name__ == "__main__":
    unittest.main()
