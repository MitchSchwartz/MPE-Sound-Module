"""fx_policy: the mod-wheel rule, the patch flag, and the inert-when-off gate."""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from patch_browser import fx_host_protocol  # noqa: E402
from patch_browser import fx_policy  # noqa: E402
from patch_browser.fx_policy import (  # noqa: E402
    CC1_TO_FX,
    CC1_TO_SURGE,
    MOD_WHEEL_EFFECT,
    MOD_WHEEL_PATCH_FIRST,
    Cc1Router,
    FxConfig,
    FxPolicy,
    cc1_destination,
    patch_uses_mod_wheel,
    resolve_config,
    route_cc1,
)
from patch_browser.patch_fx import PatchFx  # noqa: E402

# A Surge .fxp is a binary chunk header, the patch XML, then optional binary
# data. These fixtures keep that shape so the parser is tested on it.
_FXP_HEADER = b"CcnK\x00\x00\x10\x00FPChcjs3\x00\x00\x00\x01" + b"\x00" * 28
_XML_HEAD = b'<?xml version="1.0" encoding="UTF-8"?>\n<patch revision="22">\n<parameters>\n'
_XML_TAIL = b"</parameters>\n</patch>\n"
_BINARY_TAIL = b"\x00\xff\x10" * 16


def _fxp(body: bytes, *, tail: bytes = _BINARY_TAIL) -> bytes:
    return _FXP_HEADER + _XML_HEAD + body + _XML_TAIL + tail


WITH_MODWHEEL = _fxp(
    b'<a_filter1_cutoff type="2" value="3.0">\n'
    b'<modrouting source="25" depth="0.54" />\n'
    b'<modrouting source="6" depth="3.06" />\n'
    b"</a_filter1_cutoff>\n"
)
# source_index="6" is not source="6"; and a "6" in the binary tail after
# </patch> is wavetable data, not a routing.
WITHOUT_MODWHEEL = _fxp(
    b'<a_filter1_cutoff type="2" value="3.0">\n'
    b'<modrouting source="1" depth="26.0" source_index="6" />\n'
    b'<modrouting source="16" depth="0.2" />\n'
    b"</a_filter1_cutoff>\n",
    tail=b'<modrouting source="6" />',
)
NO_ROUTINGS = _fxp(b'<a_volume type="2" value="0.8" />\n')
TRUNCATED = (_FXP_HEADER + _XML_HEAD + b'<modrouting source="6" depth="1" />')[:-5]
NOT_A_PATCH = b"\x00\x01\x02 this is not a Surge patch"


class PatchModWheelFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _write(self, name: str, data: bytes) -> Path:
        path = Path(self._dir.name) / name
        path.write_bytes(data)
        return path

    def test_patch_with_a_modwheel_routing(self) -> None:
        self.assertIs(patch_uses_mod_wheel(self._write("a.fxp", WITH_MODWHEEL)), True)

    def test_patch_with_routings_but_none_from_the_wheel(self) -> None:
        self.assertIs(patch_uses_mod_wheel(self._write("b.fxp", WITHOUT_MODWHEEL)), False)

    def test_patch_with_no_routings_at_all(self) -> None:
        self.assertIs(patch_uses_mod_wheel(self._write("c.fxp", NO_ROUTINGS)), False)

    def test_unreadable_patches_are_unknown_not_false(self) -> None:
        for name, data in (("trunc.fxp", TRUNCATED), ("junk.fxp", NOT_A_PATCH), ("empty.fxp", b"")):
            with self.subTest(name):
                self.assertIsNone(patch_uses_mod_wheel(self._write(name, data)))
        self.assertIsNone(patch_uses_mod_wheel(Path(self._dir.name) / "missing.fxp"))
        self.assertIsNone(patch_uses_mod_wheel(self._dir.name))  # a directory

    def test_unreadable_patch_leaves_cc1_with_surge_under_patch_first(self) -> None:
        """Fail-safe: what CC 1 did before the FX host existed."""
        path = self._write("junk.fxp", NOT_A_PATCH)
        policy = FxPolicy(enabled=True, route_path=Path(self._dir.name) / "route.json", global_target=MOD_WHEEL_PATCH_FIRST)
        policy.on_patch_loaded(path)
        self.assertEqual(policy.destination, CC1_TO_SURGE)


class RoutingRuleTruthTableTests(unittest.TestCase):
    """target x patch-has-modwheel x channel x CC number."""

    def test_destination_table(self) -> None:
        expected = {
            (MOD_WHEEL_PATCH_FIRST, True): CC1_TO_SURGE,
            (MOD_WHEEL_PATCH_FIRST, False): CC1_TO_FX,
            (MOD_WHEEL_PATCH_FIRST, None): CC1_TO_SURGE,
            (MOD_WHEEL_EFFECT, True): CC1_TO_FX,
            (MOD_WHEEL_EFFECT, False): CC1_TO_FX,
            (MOD_WHEEL_EFFECT, None): CC1_TO_FX,
        }
        for (target, uses), dest in expected.items():
            with self.subTest(target=target, uses=uses):
                self.assertEqual(cc1_destination(target, uses), dest)

    def test_unknown_target_is_the_default(self) -> None:
        self.assertEqual(cc1_destination("bogus", True), CC1_TO_SURGE)
        self.assertEqual(cc1_destination("bogus", False), CC1_TO_FX)

    def test_only_master_channel_cc1_is_ever_consumed(self) -> None:
        targets = (MOD_WHEEL_PATCH_FIRST, MOD_WHEEL_EFFECT)
        flags = (True, False, None)
        channels = range(16)
        ccs = (1, 74, 7, 64, 11, 2)
        for target, uses, channel, cc in itertools.product(targets, flags, channels, ccs):
            dest = cc1_destination(target, uses)
            raw = [0xB0 | channel, cc, 100]
            got = route_cc1(raw, dest)
            consume = dest == CC1_TO_FX and channel == 0 and cc == 1
            with self.subTest(target=target, uses=uses, channel=channel + 1, cc=cc):
                if consume:
                    self.assertAlmostEqual(got, 100 / 127)
                else:
                    self.assertIsNone(got)

    def test_non_cc_messages_on_the_master_channel_pass(self) -> None:
        for raw in ([0x90, 1, 100], [0xE0, 1, 64], [0xD0, 1], [0xA0, 1, 100], [0xB0, 1]):
            with self.subTest(raw=raw):
                self.assertIsNone(route_cc1(raw, CC1_TO_FX))

    def test_depth_endpoints(self) -> None:
        self.assertEqual(route_cc1([0xB0, 1, 0], CC1_TO_FX), 0.0)
        self.assertEqual(route_cc1([0xB0, 1, 127], CC1_TO_FX), 1.0)


class ResolveConfigTests(unittest.TestCase):
    def test_global_default_when_patch_has_none(self) -> None:
        self.assertEqual(resolve_config(global_target=MOD_WHEEL_EFFECT).mod_wheel_target, MOD_WHEEL_EFFECT)
        self.assertEqual(resolve_config(global_target="nonsense").mod_wheel_target, MOD_WHEEL_PATCH_FIRST)

    def test_patch_value_wins_over_global(self) -> None:
        own = PatchFx(target=MOD_WHEEL_PATCH_FIRST)
        got = resolve_config(global_target=MOD_WHEEL_EFFECT, patch_config=own)
        self.assertEqual(got, FxConfig(mod_wheel_target=MOD_WHEEL_PATCH_FIRST))


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, float]] = []

    def send(self, effect_id: int, depth: float) -> bool:
        self.sent.append((effect_id, depth))
        return True


class FxPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.route = self.root / "fx-route.json"
        self.levels = self.root / "fx-levels.json"
        self.with_wheel = self.root / "with.fxp"
        self.with_wheel.write_bytes(WITH_MODWHEEL)
        self.without_wheel = self.root / "without.fxp"
        self.without_wheel.write_bytes(NO_ROUTINGS)
        prefs = self.root / "ui.json"
        patcher = mock.patch("patch_browser.ui_prefs.UI_STATE_FILE", prefs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.prefs = prefs
        self.now = 50.0

    def _policy(self, **kw) -> FxPolicy:
        kw.setdefault("enabled", True)
        kw.setdefault("route_path", self.route)
        kw.setdefault("levels_path", self.levels)
        kw.setdefault("clock", lambda: self.now)
        return FxPolicy(**kw)

    def _route(self) -> dict:
        return json.loads(self.route.read_text())

    def test_default_global_target_is_patch_first(self) -> None:
        self.assertEqual(self._policy().global_target, MOD_WHEEL_PATCH_FIRST)

    def test_patch_load_publishes_the_route(self) -> None:
        policy = self._policy()
        policy.on_patch_loaded(self.without_wheel)
        data = self._route()
        self.assertEqual(data["cc1"], CC1_TO_FX)
        self.assertEqual(data["effect"], fx_host_protocol.EFFECT_CHORUS)
        first = data["route_stamp"]
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(self._route()["cc1"], CC1_TO_SURGE)
        self.assertNotEqual(self._route()["route_stamp"], first)  # every load is a route change

    def test_screen_switch_lasts_until_the_next_load(self) -> None:
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_target(MOD_WHEEL_EFFECT)
        self.assertEqual(self._route()["cc1"], CC1_TO_FX)
        self.assertEqual(policy.global_target, MOD_WHEEL_PATCH_FIRST)  # not touched
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(policy.target, MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(self._route()["cc1"], CC1_TO_SURGE)

    def test_settings_default_persists_and_applies_now(self) -> None:
        self.prefs.write_text(json.dumps({"theme_mode": "dark"}))
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_global_default(MOD_WHEEL_EFFECT)
        self.assertEqual(policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(self._route()["cc1"], CC1_TO_FX)
        saved = json.loads(self.prefs.read_text())
        self.assertEqual(saved["mod_wheel_target"], MOD_WHEEL_EFFECT)
        self.assertEqual(saved["theme_mode"], "dark")  # other prefs survive
        # A fresh process reads it back.
        self.assertEqual(FxPolicy(enabled=True, route_path=self.route).global_target, MOD_WHEEL_EFFECT)

    def test_bad_pref_value_falls_back_to_default(self) -> None:
        self.prefs.write_text(json.dumps({"mod_wheel_target": 3}))
        self.assertEqual(FxPolicy(enabled=False).global_target, MOD_WHEEL_PATCH_FIRST)

    def test_effect_fader_is_a_request_in_the_route_file(self) -> None:
        policy = self._policy()
        policy.set_depth(0.25)
        data = self._route()
        self.assertEqual(data["depth_request"], 0.25)
        self.assertTrue(data["depth_stamp"])

    def test_dragging_writes_are_throttled_and_release_lands_the_last(self) -> None:
        policy = self._policy()
        policy.set_depth(0.1)  # first write goes out
        policy.set_depth(0.2)  # inside the throttle: held
        policy.set_depth(0.3)
        self.assertEqual(self._route()["depth_request"], 0.1)
        policy.finish_depth()
        self.assertEqual(self._route()["depth_request"], 0.3)
        self.now += fx_policy.DEPTH_PUBLISH_S
        policy.set_depth(0.4, final=True)
        self.assertEqual(self._route()["depth_request"], 0.4)

    def test_fader_follows_the_knob(self) -> None:
        policy = self._policy()
        self.levels.write_text(json.dumps({"depth": 0.75, "source": "knob", "request": None}))
        self.assertTrue(policy.poll_levels())
        self.assertEqual(policy.depth, 0.75)
        self.assertFalse(policy.poll_levels())  # throttled
        self.now += fx_policy.LEVELS_POLL_S
        self.assertFalse(policy.poll_levels())  # unchanged file: no read

    def test_held_fader_is_not_pulled_by_the_file(self) -> None:
        policy = self._policy()
        policy.set_depth(0.2)  # drag in progress
        self.levels.write_text(json.dumps({"depth": 0.9, "source": "knob", "request": None}))
        self.assertFalse(policy.poll_levels())
        self.assertEqual(policy.depth, 0.2)

    def test_stale_fader_echo_is_ignored(self) -> None:
        policy = self._policy()
        policy.set_depth(0.2, final=True)
        old = self._route()["depth_stamp"]
        self.now += 1
        policy.set_depth(0.6, final=True)
        self.levels.write_text(json.dumps({"depth": 0.2, "source": "fader", "request": old}))
        self.assertFalse(policy.poll_levels())
        self.assertEqual(policy.depth, 0.6)

    def test_policy_never_sends_to_the_fx_host(self) -> None:
        with mock.patch.object(fx_policy.FxHostSender, "send") as send, mock.patch.object(
            fx_policy.FxHostSender, "__init__", return_value=None
        ) as init:
            policy = self._policy()
            policy.on_patch_loaded(self.without_wheel)
            policy.set_depth(0.5, final=True)
            policy.finish_depth()
        init.assert_not_called()
        send.assert_not_called()

    def _levels_file(self, **data) -> None:
        self.levels.write_text(json.dumps(data))

    def test_patch_mod_follows_a_knob_move_newer_than_the_fader(self) -> None:
        policy = self._policy()
        policy.set_patch_mod(30)  # fader at t=50
        self._levels_file(patch_mod=80, patch_mod_at=49.0)  # knob before it: Surge has 30
        self.assertFalse(policy.poll_levels())
        self.assertEqual(policy.patch_mod, 30)
        self.now += fx_policy.LEVELS_POLL_S
        self._levels_file(patch_mod=80, patch_mod_at=51.0)  # knob after it
        self.assertTrue(policy.poll_levels())
        self.assertEqual(policy.patch_mod, 80)

    def test_patch_mod_repeat_value_after_a_fader_move_still_wins(self) -> None:
        policy = self._policy()
        self._levels_file(patch_mod=64, patch_mod_at=49.0)
        self.assertTrue(policy.poll_levels())
        policy.set_patch_mod(100)  # t=50
        self.now += 1
        self._levels_file(patch_mod=64, patch_mod_at=50.5)  # knob back to 64, later
        self.assertTrue(policy.poll_levels())
        self.assertEqual(policy.patch_mod, 64)

    def test_patch_mod_already_read_is_not_reapplied(self) -> None:
        """The file is rewritten when the depth moves; an old patch mod stays old."""
        policy = self._policy()
        self._levels_file(patch_mod=64, patch_mod_at=49.0)
        self.assertTrue(policy.poll_levels())
        policy.patch_mod = 10  # anything moved it since; nothing from the file should
        self.now += fx_policy.LEVELS_POLL_S
        self._levels_file(patch_mod=64, patch_mod_at=49.0, depth=0.5, source="knob", depth_at=50.0)
        policy.poll_levels()
        self.assertEqual(policy.patch_mod, 10)
        self.assertEqual(policy.depth, 0.5)

    def test_held_patch_mod_is_not_pulled(self) -> None:
        policy = self._policy()
        policy.set_patch_mod(30, final=False)
        self._levels_file(patch_mod=90, patch_mod_at=99.0)
        self.assertFalse(policy.poll_levels())
        self.assertEqual(policy.patch_mod, 30)
        policy.finish_patch_mod()
        self.assertFalse(policy.dragging_patch_mod)

    def test_held_patch_mod_does_not_hold_the_effect_fader(self) -> None:
        policy = self._policy()
        policy.set_patch_mod(30, final=False)
        self._levels_file(depth=0.7, source="knob", depth_at=99.0, patch_mod=90, patch_mod_at=99.0)
        self.assertTrue(policy.poll_levels())
        self.assertEqual(policy.depth, 0.7)
        self.assertEqual(policy.patch_mod, 30)

    def test_depth_already_read_is_not_reapplied_over_a_newer_request(self) -> None:
        policy = self._policy()
        self._levels_file(depth=0.75, source="knob", depth_at=49.0)
        self.assertTrue(policy.poll_levels())
        policy.set_depth(0.2, final=True)  # in flight to the remapper
        self.now += fx_policy.LEVELS_POLL_S
        # Rewritten for a patch mod move, before the request was applied.
        self._levels_file(depth=0.75, source="knob", depth_at=49.0, patch_mod=1, patch_mod_at=50.05)
        policy.poll_levels()
        self.assertEqual(policy.depth, 0.2)

    def test_knob_depth_older_than_the_request_is_ignored(self) -> None:
        policy = self._policy()
        policy.set_depth(0.2, final=True)  # t=50
        self._levels_file(depth=0.9, source="knob", depth_at=49.9)
        self.assertFalse(policy.poll_levels())
        self.assertEqual(policy.depth, 0.2)

    def test_malformed_levels_move_nothing(self) -> None:
        policy = self._policy()
        policy.set_patch_mod(30)
        for bad in (
            {"patch_mod": 200, "patch_mod_at": 99.0},
            {"patch_mod": True, "patch_mod_at": 99.0},
            {"patch_mod": 64.5, "patch_mod_at": 99.0},
            {"patch_mod": 64},
            {"patch_mod": 64, "patch_mod_at": "soon"},
            {"depth": 0.5, "source": "elsewhere", "depth_at": 99.0},
        ):
            with self.subTest(bad=bad):
                self.now += fx_policy.LEVELS_POLL_S
                self._levels_file(**bad)
                self.assertFalse(policy.poll_levels())
        self.assertEqual(policy.patch_mod, 30)
        self.assertEqual(policy.depth, 0.0)

    def test_knob_label_states_the_destination(self) -> None:
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(policy.knob_label, "Mod knob \u2192 Patch mod")
        policy.set_target(MOD_WHEEL_EFFECT)
        self.assertEqual(policy.knob_label, "Mod knob \u2192 Chorus")
        policy.on_patch_loaded(self.without_wheel)  # patch first, but no wheel routing
        self.assertEqual(policy.target, MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(policy.knob_label, "Mod knob \u2192 Chorus")

    def test_patch_mod_is_surge_osc_cc1_on_the_master_channel(self) -> None:
        address, args = self._policy().set_patch_mod(64.4)
        self.assertEqual(address, "/cc")
        self.assertEqual(args, [0.0, 1.0, 64.0])
        self.assertTrue(all(isinstance(a, float) for a in args))  # Surge wants floats


class EffectSelectionTests(unittest.TestCase):
    """The picker's choice: global, persisted, resolved per load, sent by the remapper."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.route = self.root / "fx-route.json"
        self.levels = self.root / "fx-levels.json"
        self.prefs = self.root / "ui.json"
        patcher = mock.patch("patch_browser.ui_prefs.UI_STATE_FILE", self.prefs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.with_wheel = self.root / "with.fxp"
        self.with_wheel.write_bytes(WITH_MODWHEEL)
        self.without_wheel = self.root / "without.fxp"
        self.without_wheel.write_bytes(NO_ROUTINGS)
        self.now = 10.0

    def _policy(self, **kw) -> FxPolicy:
        kw.setdefault("enabled", True)
        kw.setdefault("route_path", self.route)
        kw.setdefault("levels_path", self.levels)
        kw.setdefault("clock", lambda: self.now)
        return FxPolicy(**kw)

    def _route(self) -> dict:
        return json.loads(self.route.read_text())

    def test_names_come_from_the_protocol_table(self) -> None:
        self.assertEqual(set(fx_host_protocol.EFFECT_NAMES), set(fx_host_protocol.EFFECT_IDS))
        for effect_id in fx_host_protocol.EFFECT_IDS:
            self.assertEqual(fx_policy.effect_name(effect_id), fx_host_protocol.EFFECT_NAMES[effect_id])
        with mock.patch.dict(fx_host_protocol.EFFECT_NAMES, {fx_host_protocol.EFFECT_PHASER: "Swirl"}):
            self.assertEqual(fx_policy.effect_name(fx_host_protocol.EFFECT_PHASER), "Swirl")

    def test_no_module_restates_an_effect_name(self) -> None:
        """Names live in fx_host_protocol.EFFECT_NAMES; anything else reads them."""
        import re

        names = list(fx_host_protocol.EFFECT_NAMES.values())
        pattern = re.compile(r"[\"']({})[\"']".format("|".join(map(re.escape, names))))
        offenders = []
        for path in (REPO / "patch_browser").rglob("*.py"):
            if path.name == "fx_host_protocol.py":
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_default_is_chorus_and_the_route_carries_it(self) -> None:
        policy = self._policy()
        self.assertEqual(policy.effect_id, fx_host_protocol.EFFECT_CHORUS)
        self.assertEqual(policy.effect_name, "Chorus")
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(self._route()["effect"], fx_host_protocol.EFFECT_CHORUS)

    def test_choice_persists_and_reaches_the_route_file(self) -> None:
        self.prefs.write_text(json.dumps({"theme_mode": "dark", "mod_wheel_target": MOD_WHEEL_EFFECT}))
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_global_effect(fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertEqual(policy.effect_id, fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertEqual(self._route()["effect"], fx_host_protocol.EFFECT_TUBE_DRIVE)
        saved = json.loads(self.prefs.read_text())
        self.assertEqual(saved[fx_policy.EFFECT_PREF_KEY], fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertEqual(saved["theme_mode"], "dark")
        self.assertEqual(saved["mod_wheel_target"], MOD_WHEEL_EFFECT)
        # A fresh process reads it back, and its first route carries it.
        fresh = self._policy()
        self.assertEqual(fresh.effect_id, fx_host_protocol.EFFECT_TUBE_DRIVE)
        fresh.on_patch_loaded(self.without_wheel)
        self.assertEqual(self._route()["effect"], fx_host_protocol.EFFECT_TUBE_DRIVE)

    def test_every_shipped_id_round_trips(self) -> None:
        for effect_id in fx_host_protocol.EFFECT_IDS:
            with self.subTest(effect=effect_id):
                self._policy().set_global_effect(effect_id)
                self.assertEqual(self._route()["effect"], effect_id)
                self.assertEqual(fx_policy.read_route(self.route).effect_id, effect_id)
                self.assertEqual(self._policy().effect_id, effect_id)

    def test_bad_stored_value_falls_back_to_chorus(self) -> None:
        for bad in (99, -1, True, "1", 1.0, None, [1]):
            with self.subTest(value=bad):
                self.prefs.write_text(json.dumps({fx_policy.EFFECT_PREF_KEY: bad}))
                self.assertEqual(self._policy().effect_id, fx_host_protocol.EFFECT_CHORUS)

    def test_choice_is_not_a_route_change(self) -> None:
        """Only the effect moves: same target, same route stamp, so no replay to Surge."""
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_target(MOD_WHEEL_EFFECT)
        before = self._route()
        policy.set_global_effect(fx_host_protocol.EFFECT_PHASER)
        after = self._route()
        self.assertEqual(after["route_stamp"], before["route_stamp"])
        self.assertEqual(after["cc1"], before["cc1"])
        self.assertEqual(policy.target, MOD_WHEEL_EFFECT)

    def test_choice_survives_patch_loads_and_the_settings_default(self) -> None:
        policy = self._policy()
        policy.set_global_effect(fx_host_protocol.EFFECT_TREMOLO)
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(self._route()["effect"], fx_host_protocol.EFFECT_TREMOLO)
        policy.set_global_default(MOD_WHEEL_EFFECT)
        self.assertEqual(self._route()["effect"], fx_host_protocol.EFFECT_TREMOLO)
        policy.set_target(MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(self._route()["effect"], fx_host_protocol.EFFECT_TREMOLO)

    def test_patch_value_wins_over_the_global_effect(self) -> None:
        own = PatchFx(effect=fx_host_protocol.EFFECT_PHASER)
        got = resolve_config(
            global_target=MOD_WHEEL_PATCH_FIRST,
            global_effect=fx_host_protocol.EFFECT_TUBE_DRIVE,
            patch_config=own,
        )
        self.assertEqual(got.effect_id, fx_host_protocol.EFFECT_PHASER)
        got = resolve_config(global_target=MOD_WHEEL_PATCH_FIRST, global_effect=fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertEqual(got.effect_id, fx_host_protocol.EFFECT_TUBE_DRIVE)

    def test_knob_label_names_the_effect(self) -> None:
        policy = self._policy(global_target=MOD_WHEEL_EFFECT)
        policy.set_global_effect(fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertEqual(policy.knob_label, "Mod knob \u2192 Drive")
        policy.set_target(MOD_WHEEL_PATCH_FIRST)
        policy.on_patch_loaded(self.with_wheel)
        policy.set_target(MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(policy.knob_label, "Mod knob \u2192 Patch mod")

    def test_inert_when_off(self) -> None:
        policy = self._policy(enabled=False)
        policy.set_global_effect(fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertFalse(self.route.exists())
        self.assertFalse(self.prefs.exists())
        self.assertEqual(policy.effect_id, fx_host_protocol.EFFECT_CHORUS)

    def test_remapper_sends_the_chosen_effect_and_nothing_else_does(self) -> None:
        sender = _RecordingSender()
        router = Cc1Router(
            sender=sender,
            route_path=self.route,
            levels_path=self.levels,
            clock=lambda: self.now,
        )

        def tick() -> None:
            self.now += fx_policy.ROUTE_REFRESH_S
            router.refresh()

        policy = self._policy(global_target=MOD_WHEEL_EFFECT)
        policy.on_patch_loaded(self.with_wheel)
        tick()
        # A never-set patch: the default effect at depth 0.
        self.assertEqual(sender.sent, [(fx_host_protocol.EFFECT_CHORUS, 0.0)])
        self.assertTrue(router.consume([0xB0, 1, 1]))  # knob picks up 0...
        self.assertTrue(router.consume([0xB0, 1, 127]))  # ...and follows
        self.assertEqual(sender.sent[-1], (fx_host_protocol.EFFECT_CHORUS, 1.0))
        with mock.patch.object(socket_module(), "socket", side_effect=AssertionError("UI opened a socket")):
            policy.set_global_effect(fx_host_protocol.EFFECT_TUBE_DRIVE)
        tick()
        # The new effect plays at its own depth for this patch (never set: 0);
        # the knob level does not carry over. Sent once, by the remapper.
        self.assertEqual(sender.sent[-1], (fx_host_protocol.EFFECT_TUBE_DRIVE, 0.0))
        count = len(sender.sent)
        tick()
        self.assertEqual(len(sender.sent), count)
        self.assertTrue(router.consume([0xB0, 1, 64]))  # not picked up: nothing
        self.assertEqual(len(sender.sent), count)
        self.assertTrue(router.consume([0xB0, 1, 0]))  # crosses 0: follows
        self.assertTrue(router.consume([0xB0, 1, 64]))
        self.assertEqual(sender.sent[-1], (fx_host_protocol.EFFECT_TUBE_DRIVE, 64 / 127))


def socket_module():
    return fx_policy.socket


class InertWhenOffTests(unittest.TestCase):
    """MPE_FX_HOST off: no route file, no datagram, CC 1 passes to Surge."""

    def test_policy_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            route = Path(tmp) / "fx-route.json"
            levels = Path(tmp) / "fx-levels.json"
            levels.write_text(json.dumps({"depth": 0.9, "source": "knob"}))
            patch = Path(tmp) / "p.fxp"
            patch.write_bytes(NO_ROUTINGS)
            policy = FxPolicy(enabled=False, route_path=route, levels_path=levels, global_target=MOD_WHEEL_EFFECT)
            policy.on_patch_loaded(patch)
            policy.set_target(MOD_WHEEL_EFFECT)
            policy.set_depth(0.8, final=True)
            self.assertFalse(route.exists())
            self.assertFalse(policy.poll_levels())
            # The readout still knows the patch.
            self.assertIs(policy.uses_mod_wheel, False)

    def test_router_is_absent_unless_enabled(self) -> None:
        for value in ("", "0", "off", "no", "false", "2"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"MPE_FX_HOST": value}):
                self.assertIsNone(Cc1Router.from_env())
        with mock.patch.dict(os.environ, {"MPE_FX_HOST": "on"}):
            router = Cc1Router.from_env()
            self.assertIsNotNone(router)
            router._sender.close()

    def test_default_policy_follows_the_env(self) -> None:
        with mock.patch.dict(os.environ, {"MPE_FX_HOST": "0"}):
            self.assertFalse(FxPolicy(global_target=MOD_WHEEL_PATCH_FIRST).enabled)
        with mock.patch.dict(os.environ, {"MPE_FX_HOST": "1"}):
            self.assertTrue(FxPolicy(global_target=MOD_WHEEL_PATCH_FIRST).enabled)


class OneSenderGuardTests(unittest.TestCase):
    """Depth has one writer: Cc1Router, in the remapper. Refuse a second."""

    def test_only_the_router_constructs_a_sender(self) -> None:
        import ast

        tree = ast.parse((REPO / "patch_browser" / "fx_policy.py").read_text())
        owners = []
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for node in ast.walk(cls):
                if isinstance(node, ast.Name) and node.id == "FxHostSender":
                    owners.append(cls.name)
        self.assertEqual(set(owners), {"Cc1Router"})

    def test_nothing_else_in_the_repo_encodes_or_sends(self) -> None:
        allowed = {
            REPO / "patch_browser" / "fx_policy.py",
            REPO / "patch_browser" / "fx_host_protocol.py",
        }
        offenders = []
        for path in list((REPO / "patch_browser").rglob("*.py")) + list((REPO / "scripts").rglob("*.py")):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "FxHostSender" in text or "fx_host_protocol.encode" in text or "from patch_browser.fx_host_protocol import" in text and "encode" in text:
                offenders.append(str(path.relative_to(REPO)))
        self.assertEqual(offenders, [])


class Cc1RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.route = Path(self._dir.name) / "fx-route.json"
        self.levels = Path(self._dir.name) / "fx-levels.json"
        self.sender = _RecordingSender()
        self.now = 100.0
        self.router = Cc1Router(
            sender=self.sender,
            route_path=self.route,
            levels_path=self.levels,
            clock=lambda: self.now,
        )
        self._n = 0

    def _publish(self, dest: str, *, route_stamp: str | None = None, depth=None, depth_stamp=None) -> None:
        self._n += 1
        self.route.write_text(
            json.dumps(
                {
                    "cc1": dest,
                    "effect": 0,
                    "route_stamp": route_stamp or f"r{self._n}",
                    "depth_request": depth,
                    "depth_stamp": depth_stamp,
                }
            )
        )

    def _tick(self) -> None:
        self.now += fx_policy.ROUTE_REFRESH_S
        self.router.refresh()

    def test_no_route_file_means_surge(self) -> None:
        self._tick()
        self.assertFalse(self.router.consume([0xB0, 1, 90]))
        self.assertEqual(self.sender.sent, [])

    def test_follows_the_published_route(self) -> None:
        self._publish(CC1_TO_FX)
        self._tick()
        self.assertTrue(self.router.consume([0xB0, 1, 127]))
        self.assertFalse(self.router.consume([0xB1, 1, 127]))  # member channel
        self.assertFalse(self.router.consume([0xB0, 74, 127]))
        self.assertEqual(self.sender.sent, [(0, 1.0)])

    def test_refresh_is_throttled_and_change_guarded(self) -> None:
        self._publish(CC1_TO_FX)
        with mock.patch.object(fx_policy, "read_route", wraps=fx_policy.read_route) as reader:
            self.router.refresh()
            self.router.refresh()  # same instant: throttled, no stat
            self._tick()  # stat, file unchanged: no read
            self.assertEqual(reader.call_count, 1)
            self._publish(CC1_TO_FX)  # same second, new inode: still seen
            self._tick()
            self.assertEqual(reader.call_count, 2)

    def test_handing_the_wheel_to_the_effect_moves_no_depth(self) -> None:
        """A route change only says where the next knob message goes (A4)."""
        self._publish(CC1_TO_SURGE, route_stamp="r", depth=0.6, depth_stamp="d1")
        self._tick()
        self.assertFalse(self.router.consume([0xB0, 1, 0]))  # to Surge
        self._publish(CC1_TO_FX)
        self._tick()
        self.assertEqual(self.sender.sent, [(0, 0.6)])
        self.assertEqual(self.router.depth, 0.6)

    def test_handing_the_wheel_back_sends_surge_nothing(self) -> None:
        self._publish(CC1_TO_FX)
        self._tick()
        self.assertTrue(self.router.consume([0xB0, 1, 90]))
        self._tick()
        before = self._levels()
        for _ in range(2):  # the switch, then a load that stays with Surge
            self._publish(CC1_TO_SURGE)
            self._tick()
            self._tick()
        self.assertEqual(self._levels(), before)
        self.assertNotIn("patch_mod", self._levels())

    def test_fader_request_is_applied_once_and_echoed(self) -> None:
        self._publish(CC1_TO_SURGE, route_stamp="r", depth=0.4, depth_stamp="d1")
        self._tick()
        self.assertEqual(self.sender.sent, [(0, 0.4)])
        self._tick()  # flushes the echo on the next tick at the latest
        echo = json.loads(self.levels.read_text())
        self.assertEqual(
            echo, {"depth": 0.4, "source": "fader", "request": "d1", "depth_at": self.now - fx_policy.ROUTE_REFRESH_S}
        )
        # Rewritten with the same request stamp: not re-applied, not a route change.
        self._publish(CC1_TO_SURGE, route_stamp="r", depth=0.4, depth_stamp="d1")
        self._tick()
        self.assertEqual(self.sender.sent, [(0, 0.4)])

    def test_knob_moves_are_echoed_at_most_once_per_tick(self) -> None:
        self._publish(CC1_TO_FX)
        self._tick()
        with mock.patch.object(fx_policy, "atomic_write_json", wraps=fx_policy.atomic_write_json) as writer:
            for value in range(0, 128, 8):
                self.router.consume([0xB0, 1, value])
            self._tick()
            self._tick()  # nothing moved: no write
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(json.loads(self.levels.read_text())["depth"], 120 / 127)
        self.assertEqual(json.loads(self.levels.read_text())["source"], "knob")

    def test_knob_and_fader_last_one_wins(self) -> None:
        self._publish(CC1_TO_FX, route_stamp="r")
        self._tick()
        self.router.consume([0xB0, 1, 127])
        self._publish(CC1_TO_FX, route_stamp="r", depth=0.1, depth_stamp="d1")
        self._tick()
        self.assertEqual(self.router.depth, 0.1)
        # The fader moved the depth from under the knob: it picks up first.
        self.router.consume([0xB0, 1, 0])
        self.assertEqual(self.router.depth, 0.1)
        self.router.consume([0xB0, 1, 20])  # crosses 0.1 * 127
        self.assertEqual(self.router.depth, 20 / 127)
        self.router.consume([0xB0, 1, 0])
        self.assertEqual(self.router.depth, 0.0)

    def _levels(self) -> dict:
        return json.loads(self.levels.read_text())

    def test_knob_to_surge_publishes_patch_mod_once_per_tick(self) -> None:
        self._publish(CC1_TO_SURGE)
        self._tick()
        with mock.patch.object(fx_policy, "atomic_write_json", wraps=fx_policy.atomic_write_json) as writer:
            for value in range(0, 128, 8):
                self.now += 0.001
                self.assertFalse(self.router.consume([0xB0, 1, value]))
            moved_at = self.now
            self._tick()
            self._tick()  # nothing moved: no write
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self._levels(), {"patch_mod": 120, "patch_mod_at": moved_at})
        self.assertEqual(self.sender.sent, [])

    def test_no_levels_file_until_something_moves(self) -> None:
        self._publish(CC1_TO_SURGE)
        self._tick()
        self.router.consume([0xB1, 1, 64])  # member channel: not the knob
        self.router.consume([0xB0, 74, 64])
        self._tick()
        self.assertFalse(self.levels.exists())

    def test_knob_to_the_effect_leaves_patch_mod_alone(self) -> None:
        self._publish(CC1_TO_SURGE)
        self._tick()
        self.router.consume([0xB0, 1, 10])
        self._tick()
        self._publish(CC1_TO_FX)
        self._tick()
        self.router.consume([0xB0, 1, 100])
        self._tick()
        levels = self._levels()
        self.assertEqual(levels["patch_mod"], 10)
        self.assertEqual(levels["depth"], 100 / 127)
        self.assertEqual(levels["source"], "knob")

    def test_garbage_route_file_means_surge(self) -> None:
        self.route.write_text('{"cc1": "elsewhere", "effect": 0.0, "depth_request": "x", "depth_stamp": 5}')
        self._tick()
        self.assertEqual(self.router.destination, CC1_TO_SURGE)
        self.assertEqual(self.router.effect_id, 0)
        self.assertIsInstance(self.router.effect_id, int)
        self.assertEqual(self.sender.sent, [])

    def test_ui_and_remapper_round_trip(self) -> None:
        """FxPolicy writes, Cc1Router applies and echoes, FxPolicy follows."""
        clock = [10.0]
        policy = FxPolicy(
            enabled=True,
            route_path=self.route,
            levels_path=self.levels,
            global_target=MOD_WHEEL_EFFECT,
            clock=lambda: clock[0],
        )
        patch = Path(self._dir.name) / "p.fxp"
        patch.write_bytes(WITH_MODWHEEL)
        policy.on_patch_loaded(patch)
        policy.set_depth(0.3, final=True)
        self._tick()
        self._tick()
        self.assertEqual(self.sender.sent, [(0, 0.3)])
        self.assertTrue(self.router.consume([0xB0, 1, 30]))  # knob picks up 0.3...
        self.assertTrue(self.router.consume([0xB0, 1, 50]))
        self.assertTrue(self.router.consume([0xB0, 1, 127]))  # ...and is turned up
        self._tick()
        clock[0] += 1
        self.assertTrue(policy.poll_levels())
        self.assertEqual(policy.depth, 1.0)

    def test_patch_mod_round_trip_last_move_wins(self) -> None:
        """One clock, as on the appliance: knob and fader take turns."""
        policy = FxPolicy(
            enabled=True,
            route_path=self.route,
            levels_path=self.levels,
            global_target=MOD_WHEEL_PATCH_FIRST,
            clock=lambda: self.now,
        )
        patch = Path(self._dir.name) / "p.fxp"
        patch.write_bytes(WITH_MODWHEEL)
        policy.on_patch_loaded(patch)  # CC 1 -> Surge
        self._tick()

        def ui_poll() -> bool:
            self.now += fx_policy.LEVELS_POLL_S
            return policy.poll_levels()

        self.assertFalse(self.router.consume([0xB0, 1, 70]))  # knob
        self._tick()
        self.assertTrue(ui_poll())
        self.assertEqual(policy.patch_mod, 70)
        self.now += 0.01
        policy.set_patch_mod(20)  # fader, later
        self.assertFalse(ui_poll())
        self.assertEqual(policy.patch_mod, 20)
        self.now += 0.01
        self.router.consume([0xB0, 1, 71])  # knob again, later still
        self._tick()
        self.assertTrue(ui_poll())
        self.assertEqual(policy.patch_mod, 71)
        # Flip to the effect and back: nothing moves but where the knob goes.
        policy.set_patch_mod(5)
        sent = list(self.sender.sent)
        policy.set_target(MOD_WHEEL_EFFECT)
        self._tick()
        self.assertEqual(self.sender.sent, sent)
        self.assertFalse(ui_poll())
        self.assertEqual((policy.depth, policy.patch_mod), (0.0, 5))
        policy.set_target(MOD_WHEEL_PATCH_FIRST)
        self._tick()
        self.assertFalse(ui_poll())
        self.assertEqual((policy.depth, policy.patch_mod), (0.0, 5))
        # The fader set 5: the knob at 71 is ignored until it comes down to it.
        self.now += 0.01
        self.assertTrue(self.router.consume([0xB0, 1, 60]))  # swallowed
        self._tick()
        self.assertFalse(ui_poll())
        self.assertFalse(self.router.consume([0xB0, 1, 4]))  # crossed 5: forwarded
        self._tick()
        self.assertTrue(ui_poll())
        self.assertEqual(policy.patch_mod, 4)


class FxHostSenderTests(unittest.TestCase):
    def test_datagram_is_the_shared_encoding_and_deduplicated(self) -> None:
        sent: list[bytes] = []
        sender = fx_policy.FxHostSender(send=sent.append, log=None)
        self.assertTrue(sender.send(0, 0.5))
        self.assertFalse(sender.send(0, 0.5))
        self.assertTrue(sender.send(0, 0.75))
        self.assertEqual(sent, [fx_host_protocol.encode(0, 0.5), fx_host_protocol.encode(0, 0.75)])

    def test_bad_effect_id_is_logged_not_raised(self) -> None:
        said: list[str] = []
        sender = fx_policy.FxHostSender(send=lambda _p: None, log=said.append)
        self.assertFalse(sender.send(9, 0.5))
        self.assertTrue(said)

    def test_absent_fx_host_never_raises(self) -> None:
        # Nothing listens on this port; a connected UDP socket gets the refusal
        # on a later send. Either way send() returns, never raises.
        sender = fx_policy.FxHostSender(port=9, log=None)
        self.addCleanup(sender.close)
        for step in range(5):
            sender.send(0, step / 10)


def _load_remap_daemon():
    spec = importlib.util.spec_from_file_location("mpe_pressure_remap_fx", REPO / "scripts" / "mpe-pressure-remap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RemapDaemonCc1Tests(unittest.TestCase):
    """The daemon asks fx_policy, before the translator, and only when enabled."""

    def _daemon(self, env_value: str):
        with mock.patch.dict(os.environ, {"MPE_FX_HOST": env_value}):
            module = _load_remap_daemon()
            daemon = module.PressureRemapDaemon()
        emitted: list[list[int]] = []
        daemon._emit_message = lambda _binding, msg: emitted.append(list(msg))
        daemon._out = object()
        return module, daemon, emitted

    def test_off_forwards_cc1_exactly_as_before(self) -> None:
        _module, daemon, emitted = self._daemon("0")
        self.assertIsNone(daemon._cc1)
        daemon._handle_incoming(None, [0xB0, 1, 64], 0.0)
        self.assertEqual(emitted, [[0xB0, 1, 64]])

    def test_on_and_routed_to_fx_consumes_master_cc1_only(self) -> None:
        _module, daemon, emitted = self._daemon("1")
        sender = _RecordingSender()
        daemon._cc1._sender = sender
        daemon._cc1.destination = CC1_TO_FX
        daemon._handle_incoming(None, [0xB0, 1, 64], 0.0)
        daemon._handle_incoming(None, [0xB3, 1, 64], 0.0)
        daemon._handle_incoming(None, [0xB3, 74, 64], 0.0)
        daemon._handle_incoming(None, [0xB0, 74, 64], 0.0)
        self.assertEqual(emitted, [[0xB3, 1, 64], [0xB3, 74, 64], [0xB0, 74, 64]])
        self.assertEqual(sender.sent, [(0, 64 / 127)])

    def test_on_but_routed_to_surge_forwards(self) -> None:
        _module, daemon, emitted = self._daemon("1")
        daemon._cc1._sender = _RecordingSender()
        daemon._handle_incoming(None, [0xB0, 1, 64], 0.0)
        self.assertEqual(emitted, [[0xB0, 1, 64]])


if __name__ == "__main__":
    unittest.main()
