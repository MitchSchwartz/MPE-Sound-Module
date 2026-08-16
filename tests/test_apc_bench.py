"""16-pad APC footswitch bench wiring."""

import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.apc_footswitch import build_footswitches
from scripts.sooperlooper.apc_grid import loop_index_for_note, pad_note


class ApcBenchFootswitchTests(unittest.TestCase):
    def test_build_sixteen_pads(self) -> None:
        osc = MagicMock()
        midi_out = MagicMock()
        by_note, footswitches = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        self.assertEqual(len(by_note), 16)
        self.assertEqual(len(footswitches), 16)
        for note, fs in by_note.items():
            loop_i = loop_index_for_note(note)
            self.assertIsNotNone(loop_i)
            self.assertEqual(fs.loop, loop_i)
            self.assertEqual(fs._note, note)

    def test_row0_and_row3_notes(self) -> None:
        osc = MagicMock()
        midi_out = MagicMock()
        by_note, _ = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        for col in range(8):
            self.assertIn(pad_note(0, col), by_note)
            self.assertIn(pad_note(3, col), by_note)


def _bench_module():
    """Load the bench script — its filename has a hyphen, so no plain import."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "sooperlooper-apc-bench.py"
    spec = importlib.util.spec_from_file_location("sooperlooper_apc_bench", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DumpMidiTests(unittest.TestCase):
    def test_decodes_control_change(self) -> None:
        # --dump-midi is the tool used to confirm which CC each fader sends on
        # an unfamiliar APC. Rendering faders as raw hex defeats the purpose.
        bench = _bench_module()
        self.assertEqual(bench._format_midi([0xB0, 48, 100]), "ch=0 cc=48 value=100")

    def test_still_decodes_notes(self) -> None:
        bench = _bench_module()
        self.assertIn("note_on", bench._format_midi([0x90, 0, 127]))

    def test_control_change_is_not_treated_as_a_note(self) -> None:
        bench = _bench_module()
        self.assertIsNone(bench.midi_note_down(0xB0, 100))


class FaderDispatchTests(unittest.TestCase):
    """Fake CC in → assert the OSC calls, the way the footswitch tests do."""

    def setUp(self) -> None:
        from scripts.sooperlooper.apc_faders import fader_for_cc, resolve_fader_ccs
        from scripts.sooperlooper.loop_mix import CoalescingSender, LoopMix

        self.ccs, self.master, _ = resolve_fader_ccs("APC mini mk2")
        self.mix = LoopMix(num_loops=16)
        self.sent: list = []
        self.faders = CoalescingSender(
            lambda path, args: self.sent.append((path, args)), interval_s=0.0
        )
        self._fader_for_cc = fader_for_cc

    def feed(self, cc: int, value: int, *, now: float = 0.0) -> None:
        fader = self._fader_for_cc(
            cc, loop_fader_ccs=self.ccs, master_cc=self.master
        )
        if fader is None:
            return
        self.faders.submit(self.mix.messages_for(fader, value), now=now)
        self.faders.flush(now=now)

    def test_a_fader_drives_both_loops_of_its_column(self) -> None:
        self.feed(self.ccs[2], 127)  # cross pickup
        self.sent.clear()
        self.feed(self.ccs[2], 64)
        self.assertEqual([p for p, _ in self.sent], ["/sl/2/set", "/sl/10/set"])

    def test_master_drives_the_loop_bus(self) -> None:
        self.feed(self.master, 64)
        self.assertEqual([p for p, _ in self.sent], ["/set"])

    def test_non_fader_cc_is_ignored(self) -> None:
        self.feed(7, 100)
        self.assertEqual(self.sent, [])

    def test_first_touch_does_not_jump_the_level(self) -> None:
        # Faders have no motors: at startup their position is unknown, so an
        # uncrossed fader must not write.
        self.feed(self.ccs[0], 0)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
