"""Measurement harnesses are audio-safe by construction (2026-10-12).

The capacity run that day found three faults in the repo's own tools:
load-patch-osc.py dropped Surge's device-mode trim to 0 dB (a hidden +6 dB),
and midi-load-hold.py played at velocity 100 with its 16th voice on the MPE
manager channel. These tests drive the real scripts and the real PatchLoader;
only the OSC socket, the MIDI port and the amixer call are fakes.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import device_volume as dv
from patch_browser import measure_midi as mm
from patch_browser import measure_patch
from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_normalization import PatchNormalizationStore, db_to_linear

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "amixer"
AMP = ("/param/a/amp/volume", "/param/b/amp/volume")


def stage_linears(payloads: list[bytes]) -> list[float]:
    out = []
    for payload in payloads:
        word, value = payload.decode("ascii").split()
        assert word == "gain", payload
        out.append(float(value))
    return out


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    def send_message(self, address: str, value) -> None:
        self.messages.append((address, value))

    def amps(self) -> list[float]:
        return [v for a, v in self.messages if a in AMP]


class FakeMidiOut:
    instances: list["FakeMidiOut"] = []

    def __init__(self) -> None:
        self.sent: list[list[int]] = []
        self.closed = False
        FakeMidiOut.instances.append(self)

    def get_ports(self):
        return ["Midi Through:Midi Through Port-0 14:0"]

    def open_port(self, _idx):
        pass

    def send_message(self, msg):
        self.sent.append(list(msg))

    def close_port(self):
        self.closed = True


def _load_script(name: str):
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_generator(script: str, argv: list[str]) -> tuple[int, FakeMidiOut]:
    """Run a generator's main() end to end against a fake rtmidi, no real time."""
    FakeMidiOut.instances.clear()
    fake_rtmidi = types.SimpleNamespace(MidiOut=FakeMidiOut)
    module = _load_script(script)
    clock = iter(i * 0.01 for i in range(10_000_000))
    with (
        mock.patch.dict(sys.modules, {"rtmidi": fake_rtmidi}),
        mock.patch.object(sys, "argv", [script, *argv]),
        mock.patch.object(mm, "release_on_sigterm"),
        mock.patch("patch_browser.measure_midi.time.monotonic", lambda: next(clock)),
        mock.patch("patch_browser.measure_midi.time.sleep"),
    ):
        rc = module.main()
    return rc, (FakeMidiOut.instances[0] if FakeMidiOut.instances else None)


def _channel_messages(sent: list[list[int]]) -> list[tuple[int, int]]:
    """(status kind, channel nibble) for every channel-voice message."""
    return [(m[0] & 0xF0, m[0] & 0x0F) for m in sent if 0x80 <= m[0] <= 0xEF]


# --- Trim preserved in device mode -----------------------------------------


class TestPatchLoadKeepsDeviceTrim(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        store = PatchNormalizationStore(Path(self._tmp.name) / "norm.json")
        self.stage: list[bytes] = []
        self.loader = PatchLoader(normalization_store=store, fx_policy=mock.Mock(),
                                  fx_store=mock.Mock(),
                                  stage_sender=lambda p: self.stage.append(p) or True)
        self.loader.osc_client = FakeOscClient()
        self.loader.osc_enabled = True

    def _load(self, **kw) -> bool:
        with (
            mock.patch("patch_browser.patch_loader.ensure_reuse_single_patch",
                       return_value=Path("/p/Lead.fxp")),
            mock.patch("patch_browser.patch_loader.time.sleep"),
            mock.patch.object(self.loader, "_capture_hold_baseline", return_value=True),
            mock.patch.object(self.loader, "_send_hold_osc", return_value=True),
            mock.patch.object(self.loader, "_apply_playback_policy"),
            mock.patch.object(self.loader, "_resend_patch_mod"),
            mock.patch.object(self.loader, "_query_osc_float", return_value=0.9),
        ):
            return measure_patch.load_patch_like_ui(self.loader, "/p/Lead.fxp", **kw)

    def test_device_mode_every_stage_message_carries_the_trim(self):
        self.assertTrue(self._load(device_active=True, level=0.3))
        gains = stage_linears(self.stage)
        self.assertTrue(gains, "the load must set the stage")
        trim = db_to_linear(dv.DEVICE_MODE_SURGE_TRIM_DB)
        for gain in gains:
            # Unnormalized patch: full fader is unity, so every send is exactly the trim.
            self.assertAlmostEqual(gain, trim, places=7)
        self.assertLess(max(gains), 1.0, "a unity send is the hidden +6 dB")
        self.assertEqual(self.loader.osc_client.amps(), [], "Surge's volume is never set")

    def test_trim_mode_uses_the_saved_fader_without_headroom(self):
        self.assertTrue(self._load(device_active=False, level=1.0))
        self.assertEqual(stage_linears(self.stage)[-1], 1.0)

    def test_mode_comes_from_the_same_resolver_as_the_fader(self):
        """KA1 on hw:1 → device mode, read with `amixer contents` only."""
        root = Path(self._tmp.name) / "asound"
        (root / "card1").mkdir(parents=True)
        (root / "card1" / "usbid").write_text("2972:0047\n")
        calls: list[list[str]] = []

        def runner(args):
            calls.append(args)
            return (FIXTURES / "KA1.contents.txt").read_text(encoding="utf-8")

        state = {"device": "hw:1,0", "started": "1"}
        self.assertTrue(measure_patch.device_mode_active(state, runner=runner, asound_root=root))
        self.assertEqual(calls, [["-c", "1", "contents"]], "the probe must never write the DAC")
        self.assertFalse(measure_patch.device_mode_active({"device": "hw:9,0"}, runner=runner,
                                                          asound_root=root))

    def test_the_script_routes_through_the_ui_volume_path(self):
        module = _load_script("load-patch-osc.py")
        with (
            mock.patch.object(module, "ui_running", return_value=None),
            mock.patch.object(sys, "argv", ["load-patch-osc.py", __file__]),
            mock.patch("patch_browser.patch_loader.PatchLoader", return_value=self.loader),
            mock.patch.object(measure_patch, "device_mode_active", return_value=True),
            mock.patch("patch_browser.ui_prefs.load_volume_level", return_value=0.2),
            mock.patch("patch_browser.patch_loader.ensure_reuse_single_patch",
                       return_value=Path("/p/Lead.fxp")),
            mock.patch("patch_browser.patch_loader.time.sleep"),
            mock.patch.object(self.loader, "_capture_hold_baseline", return_value=True),
            mock.patch.object(self.loader, "_send_hold_osc", return_value=True),
            mock.patch.object(self.loader, "_apply_playback_policy"),
            mock.patch.object(self.loader, "_resend_patch_mod"),
            mock.patch.object(self.loader, "_query_osc_float", return_value=0.9),
        ):
            module.main()
        gains = stage_linears(self.stage)
        self.assertTrue(gains)
        self.assertTrue(all(g < 1.0 for g in gains), gains)
        self.assertEqual(self.loader.osc_client.amps(), [])

    def test_the_script_refuses_while_the_ui_runs(self):
        """The UI re-sends its own patch's stage gain every second: a patch loaded
        beside it would play at that gain (a cut patch's -20 dB lost, or worse)."""
        module = _load_script("load-patch-osc.py")
        with (
            mock.patch.object(sys, "argv", ["load-patch-osc.py", __file__]),
            mock.patch.object(module.subprocess, "run",
                              return_value=mock.Mock(returncode=0)) as run,
            mock.patch("patch_browser.patch_loader.PatchLoader") as loader_cls,
            self.assertRaises(SystemExit) as exit_,
        ):
            module.main()
        self.assertEqual(exit_.exception.code, 3)
        loader_cls.assert_not_called()
        self.assertEqual(run.call_args.args[0],
                         ["systemctl", "is-active", "--quiet", "touch-patch-browser.service"])
        # The older OLED UI's unit counts too.
        with mock.patch.object(module.subprocess, "run",
                               side_effect=[mock.Mock(returncode=3), mock.Mock(returncode=0)]):
            self.assertEqual(module.ui_running(), "patch-browser.service")
        # Neither unit active, or no systemctl at all: it loads.
        for result in (mock.Mock(returncode=3), FileNotFoundError("systemctl")):
            with self.subTest(result=result), mock.patch.object(
                    module.subprocess, "run",
                    **({"side_effect": result} if isinstance(result, Exception) else {"return_value": result})):
                self.assertIsNone(module.ui_running())

    def test_harnesses_that_load_patches_stop_the_ui_first(self):
        """Each harness that loads through load-patch-osc.py stops both UI units with
        mpe_unit_stop_for_measure (started again on exit) before its first load.
        measure-v8b-playable.sh has no restore trap: it relies on the refusal."""
        relying_on_refusal = {"measure-v8b-playable.sh"}
        missing = []
        for path in sorted(SCRIPTS.glob("measure-*.sh")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "mpe_load_patch_osc" not in text or path.name in relying_on_refusal:
                continue
            ui_stop = re.search(r"for (\w+) in [^;]*\btouch-patch-browser\.service[^;]*\bpatch-browser\.service"
                                r"; do\n\s*mpe_unit_stop_for_measure \"\$\1\"", text)
            if ui_stop is None:
                missing.append(path.name)
                continue
            # Before the first load at top level (a load inside a function runs later).
            top_level = [m.start() for m in re.finditer(r"^(?![\s#]).*\bmpe_load_patch_osc ", text, re.M)]
            if top_level and ui_stop.start() > min(top_level):
                missing.append(path.name + " (after its first load)")
        self.assertEqual(missing, [])
        self.assertTrue((SCRIPTS / "measure-v8b-playable.sh").read_text().count("mpe_load_patch_osc"))

    def test_harnesses_load_patches_only_through_the_shared_loader(self):
        """One authority: no measure script calls load-patch-osc.py or PatchLoader itself."""
        offenders = []
        for path in sorted(SCRIPTS.glob("measure-*")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"python3\s+\S*load-patch-osc\.py|PatchLoader\(", text):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "use mpe_load_patch_osc (lib/measure-run-as-user.sh)")


# --- Default velocity --------------------------------------------------------


class TestDefaultVelocity(unittest.TestCase):
    def _note_on_velocities(self, sent):
        return {m[2] for m in sent if m[0] & 0xF0 == 0x90}

    def test_hold_defaults_quiet(self):
        rc, out = _run_generator("midi-load-hold.py", ["0.2", "4"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._note_on_velocities(out.sent), {mm.DEFAULT_VELOCITY})
        self.assertEqual(mm.DEFAULT_VELOCITY, 20)

    def test_stagger_defaults_quiet(self):
        rc, out = _run_generator("midi-load.py", ["3"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._note_on_velocities(out.sent), {mm.DEFAULT_VELOCITY})

    def test_velocity_flag_changes_it(self):
        rc, out = _run_generator("midi-load-hold.py", ["0.2", "2", "--velocity", "45"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._note_on_velocities(out.sent), {45})

    def test_out_of_range_velocity_is_refused_before_the_port_opens(self):
        for bad in ("0", "128"):
            rc, out = _run_generator("midi-load-hold.py", ["0.2", "2", "--velocity", bad])
            self.assertEqual(rc, 1)
            self.assertIsNone(out)


# --- Manager channel never used ---------------------------------------------


class TestManagerChannelNeverUsed(unittest.TestCase):
    def test_full_voice_count_stays_on_member_channels(self):
        rc, out = _run_generator("midi-load-hold.py", ["0.2", str(mm.MAX_VOICES)])
        self.assertEqual(rc, 0)
        msgs = _channel_messages(out.sent)
        self.assertTrue(msgs)
        self.assertNotIn(mm.MANAGER_CHANNEL, {ch for _, ch in msgs})
        note_on_channels = [ch for kind, ch in msgs if kind == 0x90]
        self.assertEqual(note_on_channels, list(range(1, 16)), "one note per member channel")
        for m in out.sent:
            self.assertIn(m[0] & 0xF0, (0x80, 0x90, 0xB0, 0xD0, 0xE0), m)

    def test_stagger_stays_on_member_channels(self):
        _, out = _run_generator("midi-load.py", ["5"])
        self.assertNotIn(mm.MANAGER_CHANNEL, {ch for _, ch in _channel_messages(out.sent)})

    def test_more_voices_than_member_channels_is_refused(self):
        rc, out = _run_generator("midi-load-hold.py", ["0.2", str(mm.MAX_VOICES + 1)])
        self.assertEqual(rc, 1)
        self.assertIsNone(out, "refused before any MIDI port is opened")
        self.assertEqual(mm.MAX_VOICES, 15)

    def test_sender_refuses_the_manager_channel_and_overflow(self):
        out = FakeMidiOut()
        sender = mm.Sender(out)
        for bad in (mm.MANAGER_CHANNEL, 16, -1):
            with self.assertRaises(mm.ManagerChannelError):
                sender.note_on(bad, 60)
            with self.assertRaises(mm.ManagerChannelError):
                sender.all_notes_off(bad)
        self.assertEqual(out.sent, [])
        with self.assertRaises(ValueError):
            mm.member_channel(mm.MAX_VOICES)

    def test_release_reaches_every_voice_when_interrupted(self):
        out = FakeMidiOut()
        sender = mm.Sender(out)

        def boom(_s):
            raise SystemExit(143)

        with self.assertRaises(SystemExit):
            mm.hold(sender, 3, 10.0, clock=lambda: 0.0, sleep=boom)
        offs = [m for m in out.sent if m[0] & 0xF0 == 0x80]
        self.assertEqual(sorted(m[0] & 0x0F for m in offs), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
