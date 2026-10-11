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
        own = FxConfig(mod_wheel_target=MOD_WHEEL_PATCH_FIRST)
        got = resolve_config(global_target=MOD_WHEEL_EFFECT, patch_config=own)
        self.assertEqual(got, own)


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
        self.with_wheel = self.root / "with.fxp"
        self.with_wheel.write_bytes(WITH_MODWHEEL)
        self.without_wheel = self.root / "without.fxp"
        self.without_wheel.write_bytes(NO_ROUTINGS)
        prefs = self.root / "ui.json"
        patcher = mock.patch("patch_browser.ui_prefs.UI_STATE_FILE", prefs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.prefs = prefs

    def _policy(self, **kw) -> FxPolicy:
        kw.setdefault("enabled", True)
        kw.setdefault("route_path", self.route)
        kw.setdefault("sender", _RecordingSender())
        return FxPolicy(**kw)

    def test_default_global_target_is_patch_first(self) -> None:
        self.assertEqual(self._policy().global_target, MOD_WHEEL_PATCH_FIRST)

    def test_patch_load_publishes_the_route(self) -> None:
        policy = self._policy()
        policy.on_patch_loaded(self.without_wheel)
        data = json.loads(self.route.read_text())
        self.assertEqual(data["cc1"], CC1_TO_FX)
        self.assertEqual(data["effect"], fx_host_protocol.EFFECT_CHORUS)
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(json.loads(self.route.read_text())["cc1"], CC1_TO_SURGE)

    def test_screen_switch_lasts_until_the_next_load(self) -> None:
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_target(MOD_WHEEL_EFFECT)
        self.assertEqual(json.loads(self.route.read_text())["cc1"], CC1_TO_FX)
        self.assertEqual(policy.global_target, MOD_WHEEL_PATCH_FIRST)  # not touched
        policy.on_patch_loaded(self.with_wheel)
        self.assertEqual(policy.target, MOD_WHEEL_PATCH_FIRST)
        self.assertEqual(json.loads(self.route.read_text())["cc1"], CC1_TO_SURGE)

    def test_settings_default_persists_and_applies_now(self) -> None:
        self.prefs.write_text(json.dumps({"theme_mode": "dark"}))
        policy = self._policy()
        policy.on_patch_loaded(self.with_wheel)
        policy.set_global_default(MOD_WHEEL_EFFECT)
        self.assertEqual(policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(json.loads(self.route.read_text())["cc1"], CC1_TO_FX)
        saved = json.loads(self.prefs.read_text())
        self.assertEqual(saved["mod_wheel_target"], MOD_WHEEL_EFFECT)
        self.assertEqual(saved["theme_mode"], "dark")  # other prefs survive
        # A fresh process reads it back.
        self.assertEqual(FxPolicy(enabled=True, route_path=self.route).global_target, MOD_WHEEL_EFFECT)

    def test_bad_pref_value_falls_back_to_default(self) -> None:
        self.prefs.write_text(json.dumps({"mod_wheel_target": 3}))
        self.assertEqual(FxPolicy(enabled=False).global_target, MOD_WHEEL_PATCH_FIRST)

    def test_effect_fader_sends_depth(self) -> None:
        sender = _RecordingSender()
        policy = self._policy(sender=sender)
        policy.set_depth(0.25)
        policy.set_depth(7.0)
        self.assertEqual(sender.sent, [(0, 0.25), (0, 1.0)])

    def test_patch_mod_is_surge_osc_cc1_on_the_master_channel(self) -> None:
        address, args = self._policy().set_patch_mod(64.4)
        self.assertEqual(address, "/cc")
        self.assertEqual(args, [0.0, 1.0, 64.0])
        self.assertTrue(all(isinstance(a, float) for a in args))  # Surge wants floats


class InertWhenOffTests(unittest.TestCase):
    """MPE_FX_HOST off: no route file, no datagram, CC 1 passes to Surge."""

    def test_policy_writes_and_sends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            route = Path(tmp) / "fx-route.json"
            patch = Path(tmp) / "p.fxp"
            patch.write_bytes(NO_ROUTINGS)
            sender = _RecordingSender()
            policy = FxPolicy(enabled=False, route_path=route, sender=sender, global_target=MOD_WHEEL_EFFECT)
            policy.on_patch_loaded(patch)
            policy.set_target(MOD_WHEEL_EFFECT)
            policy.set_depth(0.8)
            self.assertFalse(route.exists())
            self.assertEqual(sender.sent, [])
            # The readout still knows the patch.
            self.assertIs(policy.uses_mod_wheel, False)

    def test_router_is_absent_unless_enabled(self) -> None:
        for value in ("", "0", "off", "no", "false", "2"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"MPE_FX_HOST": value}):
                self.assertIsNone(Cc1Router.from_env())
        with mock.patch.dict(os.environ, {"MPE_FX_HOST": "on"}):
            self.assertIsNotNone(Cc1Router.from_env())

    def test_default_policy_follows_the_env(self) -> None:
        with mock.patch.dict(os.environ, {"MPE_FX_HOST": "0"}):
            self.assertFalse(FxPolicy(global_target=MOD_WHEEL_PATCH_FIRST).enabled)
        with mock.patch.dict(os.environ, {"MPE_FX_HOST": "1"}):
            self.assertTrue(FxPolicy(global_target=MOD_WHEEL_PATCH_FIRST).enabled)


class Cc1RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.route = Path(self._dir.name) / "fx-route.json"
        self.sender = _RecordingSender()
        self.now = 100.0
        self.router = Cc1Router(sender=self.sender, route_path=self.route, clock=lambda: self.now)

    def _publish(self, dest: str, mtime: float) -> None:
        self.route.write_text(json.dumps({"cc1": dest, "effect": 0}))
        os.utime(self.route, (mtime, mtime))

    def test_no_route_file_means_surge(self) -> None:
        self.router.refresh()
        self.assertFalse(self.router.consume([0xB0, 1, 90]))
        self.assertEqual(self.sender.sent, [])

    def test_follows_the_published_route(self) -> None:
        self._publish(CC1_TO_FX, 1000)
        self.router.refresh()
        self.assertTrue(self.router.consume([0xB0, 1, 127]))
        self.assertFalse(self.router.consume([0xB1, 1, 127]))  # member channel
        self.assertFalse(self.router.consume([0xB0, 74, 127]))
        self.assertEqual(self.sender.sent, [(0, 1.0)])

    def test_refresh_is_throttled_and_mtime_guarded(self) -> None:
        self._publish(CC1_TO_FX, 1000)
        with mock.patch.object(fx_policy, "read_route", wraps=fx_policy.read_route) as reader:
            self.router.refresh()
            self.router.refresh()  # same instant: throttled, no stat
            self.now += fx_policy.ROUTE_REFRESH_S
            self.router.refresh()  # stat, but mtime unchanged: no read
            self.assertEqual(reader.call_count, 1)

    def test_handing_the_wheel_to_the_effect_jumps_to_the_knob(self) -> None:
        self._publish(CC1_TO_SURGE, 1000)
        self.router.refresh()
        self.assertFalse(self.router.consume([0xB0, 1, 127]))  # to Surge, remembered
        self.now += 1
        self._publish(CC1_TO_FX, 1001)
        self.router.refresh()
        self.assertEqual(self.sender.sent, [(0, 1.0)])

    def test_garbage_route_file_means_surge(self) -> None:
        self.route.write_text('{"cc1": "elsewhere", "effect": 0.0}')
        self.router.refresh()
        self.assertEqual(self.router.destination, CC1_TO_SURGE)
        self.assertEqual(self.router.effect_id, 0)
        self.assertIsInstance(self.router.effect_id, int)


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
