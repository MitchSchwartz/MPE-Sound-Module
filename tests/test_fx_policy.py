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
    """(mask, depth) per send in `sent`; the levels sent with each, in
    `levels`; the bass crossover sent with each, in `bass`."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, float]] = []
        self.levels: list[tuple[float, ...] | None] = []
        self.bass: list[float | None] = []

    def send(self, mask: int, depth: float, levels=None, bass_hz=None) -> bool:
        self.sent.append((mask, depth))
        self.levels.append(None if levels is None else tuple(levels))
        self.bass.append(bass_hz)
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
        self.assertEqual(data["mask"], 0)  # never set: the factory default, no effect
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
        self.assertEqual(policy.knob_label(), "Mod knob \u2192 Patch mod")
        policy.set_target(MOD_WHEEL_EFFECT)
        self.assertEqual(policy.knob_label(), "Mod knob \u2192 No FX")
        policy.on_patch_loaded(self.without_wheel)  # patch first, but no wheel routing
        self.assertEqual(policy.target, MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(policy.knob_label(), "Mod knob \u2192 No FX")

    def test_patch_mod_is_surge_osc_cc1_on_the_master_channel(self) -> None:
        address, args = self._policy().set_patch_mod(64.4)
        self.assertEqual(address, "/cc")
        self.assertEqual(args, [0.0, 1.0, 64.0])
        self.assertTrue(all(isinstance(a, float) for a in args))  # Surge wants floats


C, D, P, T = (1 << fx_host_protocol.EFFECT_CHORUS), (1 << fx_host_protocol.EFFECT_TUBE_DRIVE), (
    1 << fx_host_protocol.EFFECT_PHASER
), (1 << fx_host_protocol.EFFECT_TREMOLO)
F = 1 << fx_host_protocol.EFFECT_FLANGER
R = 1 << fx_host_protocol.EFFECT_ROTARY
W = 1 << fx_host_protocol.EFFECT_AUTO_WAH

#: The run order as (bit, name), written out here rather than read from the
#: protocol, so the generated rows below check it instead of restating it.
_RUN = ((D, "Tube"), (W, "Auto-wah"), (P, "Phaser"), (F, "Flanger"), (C, "Chorus"), (R, "Rotary"), (T, "Tremolo"))


def _expected(mask: int) -> tuple[str, str]:
    names = [name for bit, name in _RUN if mask & bit]
    return ("+".join(names) or "No FX", f"{len(names)} FX" if names else "No FX")


class ChainLabelTests(unittest.TestCase):
    """E3: names in run order when they fit, else a count, "No FX" when none."""

    #: Every mask -> (names, count).
    TRUTH = {
        0: ("No FX", "No FX"),
        D: ("Tube", "1 FX"),
        P: ("Phaser", "1 FX"),
        F: ("Flanger", "1 FX"),
        C: ("Chorus", "1 FX"),
        T: ("Tremolo", "1 FX"),
        D | P: ("Tube+Phaser", "2 FX"),
        D | F: ("Tube+Flanger", "2 FX"),
        D | C: ("Tube+Chorus", "2 FX"),
        D | T: ("Tube+Tremolo", "2 FX"),
        P | F: ("Phaser+Flanger", "2 FX"),
        P | C: ("Phaser+Chorus", "2 FX"),
        P | T: ("Phaser+Tremolo", "2 FX"),
        F | C: ("Flanger+Chorus", "2 FX"),
        F | T: ("Flanger+Tremolo", "2 FX"),
        C | T: ("Chorus+Tremolo", "2 FX"),
        D | P | F: ("Tube+Phaser+Flanger", "3 FX"),
        D | P | C: ("Tube+Phaser+Chorus", "3 FX"),
        D | P | T: ("Tube+Phaser+Tremolo", "3 FX"),
        D | F | C: ("Tube+Flanger+Chorus", "3 FX"),
        D | F | T: ("Tube+Flanger+Tremolo", "3 FX"),
        D | C | T: ("Tube+Chorus+Tremolo", "3 FX"),
        P | F | C: ("Phaser+Flanger+Chorus", "3 FX"),
        P | F | T: ("Phaser+Flanger+Tremolo", "3 FX"),
        P | C | T: ("Phaser+Chorus+Tremolo", "3 FX"),
        F | C | T: ("Flanger+Chorus+Tremolo", "3 FX"),
        D | P | F | C: ("Tube+Phaser+Flanger+Chorus", "4 FX"),
        D | P | F | T: ("Tube+Phaser+Flanger+Tremolo", "4 FX"),
        D | P | C | T: ("Tube+Phaser+Chorus+Tremolo", "4 FX"),
        D | F | C | T: ("Tube+Flanger+Chorus+Tremolo", "4 FX"),
        P | F | C | T: ("Phaser+Flanger+Chorus+Tremolo", "4 FX"),
        D | P | F | C | T: ("Tube+Phaser+Flanger+Chorus+Tremolo", "5 FX"),
    }
    #: The masks with the rotary or the auto-wah (2026-10-13): a few written
    #: out, the other 90 generated from `_RUN` (`full_truth`).
    TRUTH_NEW = {
        R: ("Rotary", "1 FX"),
        W: ("Auto-wah", "1 FX"),
        D | W: ("Tube+Auto-wah", "2 FX"),
        C | R: ("Chorus+Rotary", "2 FX"),
        R | T: ("Rotary+Tremolo", "2 FX"),
        W | R: ("Auto-wah+Rotary", "2 FX"),
        W | P | C | R: ("Auto-wah+Phaser+Chorus+Rotary", "4 FX"),
        D | W | P | F | C | R | T: ("Tube+Auto-wah+Phaser+Flanger+Chorus+Rotary+Tremolo", "7 FX"),
    }

    @classmethod
    def full_truth(cls) -> dict[int, tuple[str, str]]:
        out = dict(cls.TRUTH)
        for mask in range(32, 128):
            out[mask] = cls.TRUTH_NEW.get(mask, _expected(mask))
        return out

    def test_the_generator_agrees_with_the_written_rows(self) -> None:
        for mask, want in list(self.TRUTH.items()) + list(self.TRUTH_NEW.items()):
            with self.subTest(mask=mask):
                self.assertEqual(_expected(mask), want)

    def test_every_mask_both_forms(self) -> None:
        truth = self.full_truth()
        self.assertEqual(set(truth), set(range(fx_host_protocol.MASK_ALL + 1)))
        self.assertEqual(len(truth), 128)
        for mask, (names, count) in truth.items():
            with self.subTest(mask=mask):
                self.assertEqual(fx_policy.chain_label(mask), names)
                self.assertEqual(fx_policy.chain_label(mask, lambda _t: True), names)
                self.assertEqual(fx_policy.chain_label(mask, lambda _t: False), count)

    def test_fit_is_judged_on_the_names(self) -> None:
        seen: list[str] = []
        fx_policy.chain_label(C | D, lambda text: seen.append(text) or len(text) <= 12)
        self.assertEqual(seen, ["Tube+Chorus"])
        self.assertEqual(fx_policy.chain_label(C | D, lambda text: len(text) <= 12), "Tube+Chorus")
        self.assertEqual(fx_policy.chain_label(C | P, lambda text: len(text) <= 12), "2 FX")

    def test_switch_label_judges_the_whole_label(self) -> None:
        seen: list[str] = []
        label = fx_policy.mod_knob_label(CC1_TO_FX, C | D, lambda text: seen.append(text) or False)
        self.assertEqual(seen, ["Mod knob \u2192 Tube+Chorus"])
        self.assertEqual(label, "Mod knob \u2192 2 FX")
        self.assertEqual(fx_policy.mod_knob_label(CC1_TO_FX, 0), "Mod knob \u2192 No FX")
        self.assertEqual(fx_policy.mod_knob_label(CC1_TO_SURGE, C | D, lambda _t: False), "Mod knob \u2192 Patch mod")

    def test_label_lists_cover_every_state(self) -> None:
        want = {label for pair in self.full_truth().values() for label in pair}
        self.assertEqual(set(fx_policy.chain_labels()), want)
        self.assertEqual(
            set(fx_policy.mod_knob_labels()),
            {"Mod knob \u2192 Patch mod"} | {"Mod knob \u2192 " + label for label in want},
        )

    def test_names_come_from_the_protocol_table(self) -> None:
        with mock.patch.dict(fx_host_protocol.EFFECT_NAMES, {fx_host_protocol.EFFECT_PHASER: "Swirl"}):
            self.assertEqual(fx_policy.chain_label(D | P), "Tube+Swirl")

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


class ChainSelectionTests(unittest.TestCase):
    """The picker's mask: global default, persisted, resolved per load, sent by the remapper."""

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

    def test_default_is_no_fx_and_the_route_carries_it(self) -> None:
        policy = self._policy()
        self.assertEqual(policy.mask, 0)
        self.assertEqual(policy.chain_label(), "No FX")
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(self._route()["mask"], 0)

    def test_a_choice_is_the_patch_s_own_and_writes_no_global_default(self) -> None:
        """F3: a toggle reaches the route file and nothing global."""
        self.prefs.write_text(json.dumps({"theme_mode": "dark", "mod_wheel_target": MOD_WHEEL_EFFECT}))
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.toggle_effect(fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertEqual(policy.mask, D)
        self.assertEqual(self._route()["mask"], D)
        self.assertEqual(
            json.loads(self.prefs.read_text()), {"theme_mode": "dark", "mod_wheel_target": MOD_WHEEL_EFFECT}
        )
        self.assertFalse(hasattr(fx_policy, "MASK_PREF_KEY"))
        self.assertFalse(hasattr(FxPolicy, "set_global_mask"))
        # A fresh process plays the factory default for a patch it knows nothing of.
        fresh = self._policy()
        self.assertEqual(fresh.mask, 0)
        fresh.on_patch_loaded(self.without_wheel)
        self.assertEqual(self._route()["mask"], 0)

    def test_every_mask_round_trips(self) -> None:
        for mask in range(fx_host_protocol.MASK_ALL + 1):
            with self.subTest(mask=mask):
                self._policy().set_mask(mask)
                self.assertEqual(self._route()["mask"], mask)
                self.assertEqual(fx_policy.read_route(self.route).mask, mask)

    def test_a_stale_global_pref_is_never_read(self) -> None:
        """The prefs rounds 3/4 wrote (`fx_mask`, `fx_effect`) are ignored."""
        for prefs in ({"fx_mask": D | T}, {"fx_effect": 1}, {"fx_mask": 16, "fx_effect": 2}):
            with self.subTest(prefs=prefs):
                self.prefs.write_text(json.dumps(prefs))
                policy = self._policy()
                self.assertEqual(policy.mask, 0)
                policy.on_patch_loaded(self.with_wheel)
                self.assertEqual(self._route()["mask"], 0)

    def test_route_file_carries_the_mask_and_nothing_of_the_old_shape(self) -> None:
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.toggle_effect(fx_host_protocol.EFFECT_PHASER)
        self.assertEqual(
            set(self._route()),
            {
                "cc1",
                "mask",
                "levels",
                "bass_hz",
                "target",
                "uses_mod_wheel",
                "route_stamp",
                "depth_request",
                "depth_stamp",
                "patch_mod",
                "patch_mod_stamp",
                "patch_mod_at",
            },
        )
        self.assertEqual(self._route()["mask"], P)

    def test_choice_is_not_a_route_change(self) -> None:
        """Only the mask moves: same target, same route stamp, no depth request."""
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_target(MOD_WHEEL_EFFECT)
        before = self._route()
        policy.toggle_effect(fx_host_protocol.EFFECT_PHASER)
        after = self._route()
        self.assertEqual(after["route_stamp"], before["route_stamp"])
        self.assertEqual(after["depth_stamp"], before["depth_stamp"])
        self.assertEqual(after["cc1"], before["cc1"])
        self.assertEqual(policy.target, MOD_WHEEL_EFFECT)

    def test_choice_survives_the_settings_default_and_a_flip(self) -> None:
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_mask(T | D)
        self.assertEqual(self._route()["mask"], T | D)
        policy.set_global_default(MOD_WHEEL_EFFECT)
        self.assertEqual(self._route()["mask"], T | D)
        policy.set_target(MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(self._route()["mask"], T | D)

    def test_missing_fields_are_the_factory_default_never_a_global_one(self) -> None:
        got = resolve_config(global_target=MOD_WHEEL_PATCH_FIRST, patch_config=PatchFx(mask=P | C))
        self.assertEqual((got.mask, got.depth), (P | C, 0.0))
        got = resolve_config(global_target=MOD_WHEEL_PATCH_FIRST, patch_config=PatchFx(mask=0))
        self.assertEqual(got.mask, 0)
        got = resolve_config(global_target=MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(got, FxConfig(mask=0, depth=0.0))
        got = resolve_config(global_target=MOD_WHEEL_PATCH_FIRST, patch_config=PatchFx(depth=0.4))
        self.assertEqual((got.mask, got.depth), (0, 0.4))
        # The target alone keeps its global (Settings) default.
        got = resolve_config(global_target=MOD_WHEEL_EFFECT, patch_config=PatchFx(mask=D, depth=0.2))
        self.assertEqual(got.mod_wheel_target, MOD_WHEEL_EFFECT)

    def test_knob_label_names_the_chain(self) -> None:
        policy = self._policy(global_target=MOD_WHEEL_EFFECT)
        policy.set_mask(D)
        self.assertEqual(policy.knob_label(), "Mod knob \u2192 Tube")
        policy.set_mask(D | T)
        self.assertEqual(policy.knob_label(), "Mod knob \u2192 Tube+Tremolo")
        self.assertEqual(policy.knob_label(lambda _t: False), "Mod knob \u2192 2 FX")
        policy.set_mask(0)
        self.assertEqual(policy.knob_label(), "Mod knob \u2192 No FX")
        policy.on_patch_loaded(self.with_wheel)
        policy.set_target(MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(policy.knob_label(), "Mod knob \u2192 Patch mod")

    def test_inert_when_off(self) -> None:
        policy = self._policy(enabled=False)
        policy.set_mask(D)
        policy.toggle_effect(fx_host_protocol.EFFECT_TUBE_DRIVE)
        self.assertFalse(self.route.exists())
        self.assertFalse(self.prefs.exists())
        self.assertEqual(policy.mask, 0)

    def test_remapper_sends_the_chain_and_nothing_else_does(self) -> None:
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
        # A never-set patch: the factory default, no effect at depth 0.
        self.assertEqual(sender.sent, [(0, 0.0)])
        self.assertTrue(router.consume([0xB0, 1, 1]))  # first sight of the knob: anchors
        self.assertTrue(router.consume([0xB0, 1, 127]))  # no effect ticked: taken, moves nothing
        self.assertEqual(sender.sent, [(0, 0.0)])
        with mock.patch.object(socket_module(), "socket", side_effect=AssertionError("UI opened a socket")):
            policy.toggle_effect(fx_host_protocol.EFFECT_TUBE_DRIVE)
        tick()
        # Drive joins at the depth in force; sent once, by the remapper.
        self.assertEqual(sender.sent[-1], (D, 0.0))
        count = len(sender.sent)
        tick()
        self.assertEqual(len(sender.sent), count)
        self.assertTrue(router.consume([0xB0, 1, 0]))  # the knob was tracked at 127: down from 0 is 0
        self.assertEqual(len(sender.sent), count)
        self.assertTrue(router.consume([0xB0, 1, 64]))  # a toggle moves nothing: still follows
        self.assertEqual(sender.sent[-1], (D, 64 / 127))


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
        """One writer per command: the effects (`chain`, `chainl`, `fx`, `bass`)
        from the policy, the stage `gain` from PatchLoader. The calibration
        script's isolation control is the one other `gain` sender: it mutes the
        stage to prove calibration cannot hear it."""
        import re

        writers = {
            "encode_gain": {
                REPO / "patch_browser" / "patch_loader.py",
                REPO / "scripts" / "calibrate-patch-normalization.py",
            },
        }
        effects_writer = {REPO / "patch_browser" / "fx_policy.py"}
        offenders = []
        for path in list((REPO / "patch_browser").rglob("*.py")) + list((REPO / "scripts").rglob("*.py")):
            if path == REPO / "patch_browser" / "fx_host_protocol.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            names = set(re.findall(r"\bfx_host_protocol\.(encode\w*)", text))
            if re.search(r"from patch_browser\.fx_host_protocol import[^\n]*\bencode", text):
                names.add("import encode")
            if "FxHostSender" in text:
                names.add("FxHostSender")
            for name in names:
                if path not in writers.get(name, effects_writer):
                    offenders.append(f"{path.relative_to(REPO)}: {name}")
        self.assertEqual(offenders, [])
        # Both gain writers really do write it: the guard is not vacuous.
        for path in writers["encode_gain"]:
            self.assertIn("fx_host_protocol.encode_gain", path.read_text(encoding="utf-8"), path)


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

    def _publish(self, dest: str, *, route_stamp: str | None = None, depth=None, depth_stamp=None, mask=1) -> None:
        self._n += 1
        self.route.write_text(
            json.dumps(
                {
                    "cc1": dest,
                    "mask": mask,
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
        self.assertEqual(self.sender.sent, [(C, 1.0)])

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
        self.assertEqual(self.sender.sent, [(C, 0.6)])
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
        self.assertEqual(self.sender.sent, [(C, 0.4)])
        self._tick()  # flushes the echo on the next tick at the latest
        echo = json.loads(self.levels.read_text())
        self.assertEqual(
            echo, {"depth": 0.4, "source": "fader", "request": "d1", "depth_at": self.now - fx_policy.ROUTE_REFRESH_S}
        )
        # Rewritten with the same request stamp: not re-applied, not a route change.
        self._publish(CC1_TO_SURGE, route_stamp="r", depth=0.4, depth_stamp="d1")
        self._tick()
        self.assertEqual(self.sender.sent, [(C, 0.4)])

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
        self.router.consume([0xB0, 1, 127])  # nothing in force yet: the knob is the value
        self.assertEqual(self.router.depth, 1.0)
        self._publish(CC1_TO_FX, route_stamp="r", depth=0.1, depth_stamp="d1")
        self._tick()
        self.assertEqual(self.router.depth, 0.1)
        # The fader moved the depth from under the knob: the knob's next move
        # scales from 0.1, it does not jump to where the knob is.
        self.router.consume([0xB0, 1, 120])
        self.assertAlmostEqual(self.router.depth, 0.1 * 120 / 127)
        self.router.consume([0xB0, 1, 0])
        self.assertEqual(self.router.depth, 0.0)
        self.router.consume([0xB0, 1, 127])
        self.assertEqual(self.router.depth, 1.0)

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
        self.route.write_text('{"cc1": "elsewhere", "mask": 1.0, "depth_request": "x", "depth_stamp": 5}')
        self._tick()
        self.assertEqual(self.router.destination, CC1_TO_SURGE)
        self.assertEqual(self.router.mask, fx_host_protocol.MASK_NONE)
        self.assertIsInstance(self.router.mask, int)
        self.assertEqual(self.sender.sent, [])

    def test_a_mask_naming_an_unshipped_effect_is_bypass(self) -> None:
        for bad in (fx_host_protocol.MASK_ALL + 1, -1, True, "5", None):
            with self.subTest(mask=bad):
                self.route.write_text(json.dumps({"cc1": "fx", "mask": bad, "route_stamp": f"r{bad}"}))
                self.assertEqual(fx_policy.read_route(self.route).mask, fx_host_protocol.MASK_NONE)

    def test_a_mask_change_sends_the_chain_at_the_depth_in_force(self) -> None:
        self._publish(CC1_TO_FX, route_stamp="r", depth=0.5, depth_stamp="d1", mask=C)
        self._tick()
        self._publish(CC1_TO_FX, route_stamp="r", depth=0.5, depth_stamp="d1", mask=C | P | T)
        self._tick()
        self.assertEqual(self.sender.sent, [(C, 0.5), (C | P | T, 0.5)])
        self._publish(CC1_TO_FX, route_stamp="r", depth=0.5, depth_stamp="d1", mask=0)
        self._tick()
        self.assertEqual(self.sender.sent[-1], (0, 0.5))

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
        policy.set_mask(D)
        policy.set_depth(0.3, final=True)
        self._tick()
        self._tick()
        self.assertEqual(self.sender.sent, [(D, 0.3)])
        self.assertTrue(self.router.consume([0xB0, 1, 30]))  # first sight of the knob: anchors...
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
        # The fader set 5: the knob at 71 turned down to 0 takes Surge down
        # from 5, scaled, never up to where the knob is.
        self.now += 0.01
        msg = [0xB0, 1, 36]
        self.assertFalse(self.router.consume(msg))  # half the way down: forwarded, scaled
        self.assertEqual(msg[2], round(5 * 36 / 71))
        self.assertFalse(self.router.consume([0xB0, 1, 0]))
        self._tick()
        self.assertTrue(ui_poll())
        self.assertEqual(policy.patch_mod, 0)


class FxHostSenderTests(unittest.TestCase):
    def test_datagram_is_the_chain_encoding_and_deduplicated(self) -> None:
        sent: list[bytes] = []
        sender = fx_policy.FxHostSender(send=sent.append, log=None)
        ones = fx_host_protocol.LEVELS_DEFAULT
        half_drive = (1.0, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0)
        self.assertTrue(sender.send(C, 0.5))
        self.assertFalse(sender.send(C, 0.5))
        self.assertFalse(sender.send(C, 0.5, ones))  # no levels is every level 1.0
        self.assertTrue(sender.send(C, 0.75))
        self.assertTrue(sender.send(C | T, 0.75))  # same depth, new mask: sent
        self.assertTrue(sender.send(C | T, 0.75, half_drive))  # same mask and depth, new levels: sent
        self.assertFalse(sender.send(C | T, 0.75, list(half_drive)))
        self.assertTrue(sender.send(0, 0.75, half_drive))
        self.assertEqual(
            sent,
            [
                fx_host_protocol.encode_chain_levels(C, 0.5, ones),
                fx_host_protocol.encode_chain_levels(C, 0.75, ones),
                fx_host_protocol.encode_chain_levels(C | T, 0.75, ones),
                fx_host_protocol.encode_chain_levels(C | T, 0.75, half_drive),
                fx_host_protocol.encode_chain_levels(0, 0.75, half_drive),
            ],
        )
        self.assertEqual(
            sent[3], b"chainl 9 0.750000 1.000000 0.500000 1.000000 1.000000 1.000000 1.000000 1.000000"
        )

    def test_bad_mask_is_logged_not_raised(self) -> None:
        said: list[str] = []
        sender = fx_policy.FxHostSender(send=lambda _p: None, log=said.append)
        self.assertFalse(sender.send(fx_host_protocol.MASK_ALL + 1, 0.5))
        self.assertFalse(sender.send(C, float("nan")))
        self.assertFalse(sender.send(C, 0.5, (1.0, float("nan"), 1.0, 1.0, 1.0, 1.0, 1.0)))
        self.assertFalse(sender.send(C, 0.5, (1.0, 1.0)))
        self.assertEqual(len(said), 4)

    def test_bass_rides_with_every_chain_send_so_a_restarted_host_relearns_it(self) -> None:
        """Review of #185 (P1): the fx host starts Off. After it restarts, the
        next chain datagram it gets must be preceded by `bass`, even though the
        crossover has not changed since the sender last delivered it."""
        import socket as socketmod

        def listen(port: int = 0) -> socketmod.socket:
            sock = socketmod.socket(socketmod.AF_INET, socketmod.SOCK_DGRAM)
            sock.setsockopt(socketmod.SOL_SOCKET, socketmod.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.settimeout(0.5)
            return sock

        def drain(sock: socketmod.socket) -> list[bytes]:
            got: list[bytes] = []
            sock.settimeout(0.2)
            while True:
                try:
                    got.append(sock.recv(4096))
                except (socketmod.timeout, BlockingIOError):
                    return got

        host = listen()
        port = host.getsockname()[1]
        sender = fx_policy.FxHostSender(host="127.0.0.1", port=port, log=None)
        self.addCleanup(sender.close)
        sender.send(C, 0.5, bass_hz=120)
        first = drain(host)
        self.assertEqual([p.split()[0] for p in first], [b"bass", b"chainl"])

        # The fx host restarts: a fresh process on the same port, starting Off.
        host.close()
        host = listen(port)
        self.addCleanup(host.close)
        sender.send(C, 0.5, bass_hz=120)  # nothing new: nothing sent
        self.assertEqual(drain(host), [])
        sender.send(C, 0.6, bass_hz=120)  # the chain moves: bass rides along
        after = drain(host)
        self.assertEqual(after, [fx_host_protocol.encode_bass(120),
                                 fx_host_protocol.encode_chain_levels(C, 0.6, fx_host_protocol.LEVELS_DEFAULT)])

    def test_bass_alone_is_still_deduplicated(self) -> None:
        sent: list[bytes] = []
        sender = fx_policy.FxHostSender(send=sent.append, log=None)
        sender.send(C, 0.5, bass_hz=120)
        sender.send(C, 0.5, bass_hz=120)
        sender.send(C, 0.5, bass_hz=150)
        self.assertEqual([p.split()[0] for p in sent], [b"bass", b"chainl", b"bass"])

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
        daemon._cc1.mask = C | D
        daemon._handle_incoming(None, [0xB0, 1, 64], 0.0)
        daemon._handle_incoming(None, [0xB3, 1, 64], 0.0)
        daemon._handle_incoming(None, [0xB3, 74, 64], 0.0)
        daemon._handle_incoming(None, [0xB0, 74, 64], 0.0)
        self.assertEqual(emitted, [[0xB3, 1, 64], [0xB3, 74, 64], [0xB0, 74, 64]])
        self.assertEqual(sender.sent, [(C | D, 64 / 127)])

    def test_the_datagram_on_the_wire_is_the_chain_encoding(self) -> None:
        """The remapper's real sender, route file to bytes: `chainl <mask> <depth> <levels>`."""
        with tempfile.TemporaryDirectory() as tmp:
            route = Path(tmp) / "fx-route.json"
            route.write_text(
                json.dumps({"cc1": "fx", "mask": D | T, "levels": [1.0, 0.25, 1.0, 0.5, 1.0], "route_stamp": "r1"})
            )
            with mock.patch.object(fx_policy, "route_state_path", return_value=route), mock.patch.object(
                fx_policy, "levels_state_path", return_value=Path(tmp) / "fx-levels.json"
            ):
                _module, daemon, emitted = self._daemon("1")
            wire: list[bytes] = []
            daemon._cc1._sender = fx_policy.FxHostSender(send=wire.append, log=None)
            daemon._cc1.refresh(1e6)
            daemon._handle_incoming(None, [0xB0, 1, 127], 0.0)
        self.assertEqual(emitted, [])
        # A five-entry levels list (written before the rotary and the
        # auto-wah) reads them as 1.0; a route with no bass_hz (written
        # before the bass bypass) plays at the default crossover, sent first.
        self.assertEqual(
            wire,
            [
                b"bass 120.000000",
                b"chainl 10 1.000000 1.000000 0.250000 1.000000 0.500000 1.000000 1.000000 1.000000",
            ],
        )

    def test_on_but_routed_to_surge_forwards(self) -> None:
        _module, daemon, emitted = self._daemon("1")
        daemon._cc1._sender = _RecordingSender()
        daemon._handle_incoming(None, [0xB0, 1, 64], 0.0)
        self.assertEqual(emitted, [[0xB0, 1, 64]])


if __name__ == "__main__":
    unittest.main()
