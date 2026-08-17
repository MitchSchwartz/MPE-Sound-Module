"""16-pad APC footswitch bench wiring."""

import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.apc_footswitch import apply_view, build_footswitches
from scripts.sooperlooper.apc_grid import GridView, pad_note


class ApcBenchFootswitchTests(unittest.TestCase):
    def test_build_sixteen_tracks_eight_of_them_on_pads(self) -> None:
        osc = MagicMock()
        midi_out = MagicMock()
        by_note, footswitches = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        # A footswitch per track — banked-off tracks keep their state and
        # keep receiving engine updates; only their pad binding goes away.
        self.assertEqual(len(footswitches), 16)
        self.assertEqual(len(by_note), 8)
        view = GridView()
        for note, fs in by_note.items():
            self.assertEqual(fs.loop, view.loop_for_note(note))
            self.assertEqual(fs._note, note)
        self.assertIsNone({fs.loop: fs for fs in footswitches}[15]._note)

    def test_bottom_row_only(self) -> None:
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
            self.assertNotIn(pad_note(3, col), by_note)

    def test_apply_view_rebinds_and_clears_the_old_bank(self) -> None:
        osc = MagicMock()
        midi_out = MagicMock()
        _by_note, footswitches = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        midi_out.reset_mock()
        by_note = apply_view(
            midi_out, footswitches=footswitches, view=GridView(offset=8)
        )
        self.assertEqual(sorted(fs.loop for fs in by_note.values()), list(range(8, 16)))
        self.assertEqual(by_note[pad_note(0, 0)].loop, 8)
        # Every clip pad is cleared before the repaint — a pad left lit from
        # the previous bank is a track the player thinks is running and isn't.
        cleared = [
            c.args[0][1]
            for c in midi_out.send_message.call_args_list
            if c.args[0][2] == 0
        ]
        for col in range(8):
            self.assertIn(pad_note(0, col), cleared)
        self.assertIsNone({fs.loop: fs for fs in footswitches}[0]._note)


    def test_banking_while_a_pad_is_held_does_not_clear_that_loop(self) -> None:
        # Two-handed: one finger on a clip pad, the other on Down. The pad-down
        # belongs to a gesture the player never finished; if it survives the
        # bank change, poll_hold() fires the long-press ~2 s later and wipes a
        # track that is no longer even on screen.
        osc = MagicMock()
        midi_out = MagicMock()
        by_note, footswitches = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1.0,
            debounce_ms=0.0,
        )
        held = by_note[pad_note(0, 0)]
        held.on_pad_down()
        self.assertTrue(held._pad_down)
        osc.reset_mock()  # the down leg already fired record — legitimately

        new_by_note = apply_view(
            midi_out, footswitches=footswitches, view=GridView(offset=8)
        )
        held._pad_down_at -= 10.0  # well past hold_ms
        for fs in footswitches:
            fs.poll_hold()
        self.assertFalse(held._hold_fired)
        self.assertNotIn("/sl/0/hit", [c.args[0] for c in osc.send_message.call_args_list])

        # The note-off now lands on whoever took that pad — and must do nothing.
        took_over = new_by_note[pad_note(0, 0)]
        osc.reset_mock()
        took_over.on_pad_up()
        osc.send_message.assert_not_called()


class ViewAgreementTests(unittest.TestCase):
    """The pad layer and the fader layer must address the same track.

    Every other test here checks one layer alone, so the bench forgetting to
    call `mix.set_view()` alongside `apply_view()` — the exact bug where the
    clip travels and the volume doesn't — passes all of them.
    """

    def test_pads_and_faders_address_the_same_loops_after_banking(self) -> None:
        from scripts.sooperlooper.loop_mix import LoopMix

        osc = MagicMock()
        midi_out = MagicMock()
        _by_note, footswitches = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        mix = LoopMix(num_loops=16)
        for offset in (0, 8, 1, 7):
            view = GridView(offset=offset)
            by_note = apply_view(midi_out, footswitches=footswitches, view=view)
            mix.set_view(view)
            for col in range(8):
                self.assertEqual(
                    (by_note[pad_note(0, col)].loop,),
                    mix.view.loops_for_column(col),
                    f"column {col} disagrees at offset {offset}",
                )


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
            lambda path, args: self.sent.append((path, args)),
            interval_s=0.0,
            smooth_tau_s=0.0,
        )
        self._fader_for_cc = fader_for_cc

    def feed(self, cc: int, value: int, *, now: float = 0.0) -> None:
        fader = self._fader_for_cc(
            cc, loop_fader_ccs=self.ccs, master_cc=self.master
        )
        if fader is None:
            return
        self.faders.submit(self.mix.messages_for(fader, value), now=now)
        self.faders.tick(now=now)

    def test_a_fader_drives_the_one_track_in_its_column(self) -> None:
        self.feed(self.ccs[2], 64)  # anchor
        self.sent.clear()
        self.feed(self.ccs[2], 50)
        self.assertEqual([p for p, _ in self.sent], ["/sl/2/set"])

    def test_master_drives_every_loop(self) -> None:
        self.feed(self.master, 64)
        self.assertEqual([p for p, _ in self.sent], [f"/sl/{n}/set" for n in range(16)])

    def test_non_fader_cc_is_ignored(self) -> None:
        self.feed(7, 100)
        self.assertEqual(self.sent, [])

    def test_first_touch_does_not_jump_the_level(self) -> None:
        self.feed(self.ccs[0], 40)
        self.assertEqual(self.sent, [])
        self.feed(self.ccs[0], 30)
        self.assertTrue(self.sent)


if __name__ == "__main__":
    unittest.main()
