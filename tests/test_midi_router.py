"""Per-source dispatch: the classic path is added without disturbing the MPE one."""

import json
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from midi_device import KIND_CLASSIC, KIND_MPE, Classification  # noqa: E402
from midi_router import SourceBinding, bind_source  # noqa: E402
from patch_browser.pressure_midi import remap_midi_message  # noqa: E402

FIXTURE = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures"
    / "apc-mini-mk2-notes-2026-08-28.jsonl"
)

CLASSIC = Classification(kind=KIND_CLASSIC, reason="test", member_channels=())
MPE = Classification(kind=KIND_MPE, reason="test", member_channels=tuple(range(1, 16)))


class MpePathUnchanged(unittest.TestCase):
    """Phase 2's gate: the ROLI path is unchanged *by construction*.

    There is no recorded ROLI stream to diff against (see the phase 2
    gate note in docs/CLASSIC-MIDI-PLAN.md), so this proves the weaker
    but real claim: for every message shape, an MPE binding produces
    exactly what the pre-router daemon's `remap_midi_message` call
    produced, for the same floor.
    """

    def setUp(self):
        self.binding = bind_source("LUMI Keys BLOCK MIDI 1", MPE)

    def test_mpe_binding_is_byte_identical_to_the_legacy_path(self):
        rng = random.Random(20260828)
        floors = (0.0, 0.25, 0.5, 1.0)
        checked = 0
        for status in range(0x80, 0x100):
            for _ in range(8):
                body = [rng.randrange(128) for _ in range(2)]
                raw = [status] + body
                for floor in floors:
                    legacy = remap_midi_message(list(raw), floor)
                    expected = [legacy] if legacy else []
                    self.assertEqual(
                        self.binding.apply(list(raw), floor), expected, raw
                    )
                    checked += 1
        self.assertGreater(checked, 4000, "guard against a vacuous sweep")

    def test_mpe_binding_never_fans_out(self):
        for raw in ([0x90, 60, 100], [0xD1, 40], [0xE1, 0, 64], [0xB0, 64, 127]):
            self.assertEqual(len(self.binding.apply(raw, 0.5)), 1, raw)

    def test_mpe_binding_holds_no_state_to_reset(self):
        self.binding.apply([0x91, 60, 100], 0.0)
        self.assertEqual(self.binding.reset(), [])

    def test_pressure_floor_still_reaches_the_mpe_path(self):
        """The floor is the remapper's whole reason for existing; a
        binding that silently dropped it would pass a bytes-equal test
        against itself but break the instrument."""
        low = self.binding.apply([0xD1, 1], 0.0)
        high = bind_source("x", MPE).apply([0xD1, 1], 0.9)
        self.assertNotEqual(low, high)


class ClassicPath(unittest.TestCase):
    def setUp(self):
        self.binding = bind_source("APC mini mk2 Notes", CLASSIC)

    def test_classic_notes_move_off_the_master_channel(self):
        out = self.binding.apply([0x90, 60, 100], 0.0)
        self.assertEqual(len(out), 1)
        self.assertNotEqual(out[0][0] & 0x0F, 0)

    def test_classic_binding_fans_out(self):
        """A note arriving mid-bend must emit the bend before the note."""
        self.binding.apply([0xE0, 0x00, 0x60], 0.0)
        out = self.binding.apply([0x90, 60, 100], 0.0)
        self.assertGreater(len(out), 1)
        self.assertEqual(out[0][0] & 0xF0, 0xE0)
        self.assertEqual(out[-1][0] & 0xF0, 0x90)

    def test_reset_releases_held_notes(self):
        self.binding.apply([0x90, 60, 100], 0.0)
        self.binding.apply([0x90, 64, 100], 0.0)
        released = self.binding.reset()
        self.assertEqual(len(released), 2)
        for msg in released:
            self.assertIn(msg[0] & 0xF0, (0x80, 0x90))

    def test_golden_stream_survives_the_binding(self):
        stream = [
            json.loads(line)["msg"]
            for line in FIXTURE.read_text().splitlines()
            if line.strip()
        ]
        out = []
        for msg in stream:
            out.extend(self.binding.apply(msg, 0.0))
        # Counts match by coincidence -- 7 retriggers add a message each
        # and 7 duplicate note_offs drop one each. Assert the shape, not
        # the total, or this proves nothing.
        self.assertEqual(len(out), 187)
        self.assertEqual(self.binding.messages_seen, len(stream))
        for msg in out:
            self.assertNotEqual(msg[0] & 0x0F, 0, "master channel must stay clear")


class Isolation(unittest.TestCase):
    def test_two_classic_sources_do_not_share_channel_state(self):
        """Independent allocators would double-book member channels and
        cut each other's notes off."""
        a = bind_source("keyboard A", CLASSIC)
        b = bind_source("keyboard B", CLASSIC)
        ca = a.apply([0x90, 60, 100], 0.0)[0][0] & 0x0F
        cb = b.apply([0x90, 62, 100], 0.0)[0][0] & 0x0F
        self.assertEqual(ca, cb, "separate translators allocate independently")
        self.assertIsNot(a.translator, b.translator)

    def test_binding_kind_and_counters(self):
        a = bind_source("k", CLASSIC)
        m = bind_source("r", MPE)
        self.assertTrue(a.is_classic)
        self.assertFalse(m.is_classic)
        self.assertEqual(a.messages_seen, 0)
        a.apply([0x90, 60, 100], 0.0)
        self.assertEqual(a.messages_seen, 1)


if __name__ == "__main__":
    unittest.main()


REAL_PORTS = (
    "Midi Through:Midi Through Port-0",
    "Scarlett 4i4 USB:Scarlett 4i4 USB MIDI 1",
    "APC mini mk2:APC mini mk2 Control",
    "APC mini mk2:APC mini mk2 Notes",
    "LUMI Keys BLOCK:LUMI Keys BLOCK MIDI 1",
    "RtMidiOut Client:RtMidi output",
)


def _is_mpe(name: str) -> bool:
    return "lumi" in name.lower()


class PortSelection(unittest.TestCase):
    """The other half of the phase 2 gate: which ports get bound at all."""

    def test_flag_off_binds_exactly_what_the_old_daemon_bound(self):
        from midi_router import select_router_ports

        self.assertEqual(
            select_router_ports(REAL_PORTS, route_classic=False, is_mpe_port=_is_mpe),
            ["LUMI Keys BLOCK:LUMI Keys BLOCK MIDI 1"],
        )

    def test_flag_on_adds_the_apc_notes_port_but_never_control(self):
        from midi_router import select_router_ports

        selected = select_router_ports(
            REAL_PORTS, route_classic=True, is_mpe_port=_is_mpe
        )
        self.assertIn("APC mini mk2:APC mini mk2 Notes", selected)
        self.assertNotIn("APC mini mk2:APC mini mk2 Control", selected)

    def test_loopback_and_own_output_are_never_bound(self):
        from midi_router import select_router_ports

        for route_classic in (False, True):
            selected = select_router_ports(
                REAL_PORTS, route_classic=route_classic, is_mpe_port=_is_mpe
            )
            self.assertNotIn("Midi Through:Midi Through Port-0", selected)
            self.assertNotIn("RtMidiOut Client:RtMidi output", selected)


class Reconnect(unittest.TestCase):
    """Hot-plug decisions. The previous logic was ROLI-shaped -- 'no ROLI
    on the bus' meant 'close every input' -- which would have torn down a
    classic keyboard's port when the MPE controller was unplugged."""

    def setUp(self):
        from midi_router import (
            RECONNECT_CLOSE,
            RECONNECT_IDLE,
            RECONNECT_REOPEN,
            reconnect_decision,
        )

        self.decide = reconnect_decision
        self.IDLE, self.CLOSE, self.REOPEN = (
            RECONNECT_IDLE,
            RECONNECT_CLOSE,
            RECONNECT_REOPEN,
        )

    def test_steady_state_does_nothing(self):
        self.assertEqual(
            self.decide(("a", "b"), ("a", "b"), have_inputs=True), self.IDLE
        )

    def test_unplugging_one_device_does_not_tear_down_the_other(self):
        """The regression this function exists to prevent."""
        self.assertEqual(
            self.decide(("APC Notes",), ("APC Notes", "LUMI"), have_inputs=True),
            self.REOPEN,
        )

    def test_last_device_removed_closes(self):
        self.assertEqual(self.decide((), ("LUMI",), have_inputs=True), self.CLOSE)

    def test_nothing_attached_and_nothing_open_is_idle(self):
        """Must not print or churn every poll on a bare appliance."""
        self.assertEqual(self.decide((), (), have_inputs=False), self.IDLE)

    def test_new_device_appearing_reopens(self):
        self.assertEqual(
            self.decide(("APC Notes", "LUMI"), ("LUMI",), have_inputs=True),
            self.REOPEN,
        )

    def test_names_match_but_ports_died_reopens(self):
        """Bindings gone while the bus still lists the port -- the
        subscription-failure shape seen elsewhere on this appliance."""
        self.assertEqual(
            self.decide(("LUMI",), ("LUMI",), have_inputs=False), self.REOPEN
        )
