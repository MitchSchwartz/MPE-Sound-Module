"""Classic -> MPE translation. Pure; no ports, no Pi, no audio graph."""

import unittest

from scripts.midi_translate import (
    BEND_CENTRE,
    CC_DATA_ENTRY_MSB,
    CC_EXPRESSION,
    CC_MOD_WHEEL,
    CC_RPN_LSB,
    CC_RPN_MSB,
    CC_SUSTAIN,
    CHANNEL_PRESSURE,
    CONTROL_CHANGE,
    MASTER_CHANNEL,
    NOTE_OFF,
    NOTE_ON,
    PITCH_BEND,
    POLY_AFTERTOUCH,
    PROGRAM_CHANGE,
    STEAL_NEVER,
    STEAL_NEWEST,
    ClassicToMpe,
    mpe_configuration_message,
    scale_bend,
)


def bend_msg(channel: int, value14: int) -> list[int]:
    return [PITCH_BEND | channel, value14 & 0x7F, (value14 >> 7) & 0x7F]


def value14(msg: list[int]) -> int:
    return (msg[2] << 7) | msg[1]


class BendScaling(unittest.TestCase):
    def test_centre_is_unchanged(self) -> None:
        self.assertEqual(scale_bend(BEND_CENTRE, 2, 48), BEND_CENTRE)

    def test_full_classic_bend_becomes_one_twentyfourth_of_the_range(self) -> None:
        """The headline bug: +/-2 read against +/-48 is 24x too wide."""
        full_up = scale_bend(16383, 2, 48)
        self.assertAlmostEqual((full_up - BEND_CENTRE) / (16383 - BEND_CENTRE),
                               2 / 48, places=3)

    def test_symmetric_down(self) -> None:
        self.assertAlmostEqual(BEND_CENTRE - scale_bend(0, 2, 48),
                               scale_bend(16383, 2, 48) - BEND_CENTRE, delta=1)

    def test_equal_ranges_are_identity(self) -> None:
        for v in (0, 4096, BEND_CENTRE, 12000, 16383):
            self.assertEqual(scale_bend(v, 48, 48), v)

    def test_never_leaves_14_bit_range(self) -> None:
        for v in (0, 16383):
            out = scale_bend(v, 96, 48)
            self.assertGreaterEqual(out, 0)
            self.assertLessEqual(out, 16383)


class NoteAllocation(unittest.TestCase):
    def setUp(self) -> None:
        self.t = ClassicToMpe()

    def test_each_note_gets_its_own_member_channel(self) -> None:
        out = []
        for note in (60, 64, 67):
            out += self.t.translate([NOTE_ON, note, 100])
        channels = {m[0] & 0x0F for m in out if m[0] & 0xF0 == NOTE_ON}
        self.assertEqual(len(channels), 3)
        self.assertNotIn(MASTER_CHANNEL, channels, "notes must not land on master")

    def test_note_off_releases_the_right_channel(self) -> None:
        self.t.translate([NOTE_ON, 60, 100])
        ch = self.t.active_notes[60]
        out = self.t.translate([NOTE_OFF, 60, 0])
        self.assertEqual(out, [[NOTE_OFF | ch, 60, 0]])
        self.assertEqual(self.t.active_notes, {})

    def test_note_on_velocity_zero_is_a_note_off(self) -> None:
        self.t.translate([NOTE_ON, 60, 100])
        ch = self.t.active_notes[60]
        self.assertEqual(self.t.translate([NOTE_ON, 60, 0]), [[NOTE_OFF | ch, 60, 0]])

    def test_a_released_channel_is_not_reused_immediately(self) -> None:
        """A release tail is still sounding; reassigning re-modulates it."""
        self.t.translate([NOTE_ON, 60, 100])
        first = self.t.active_notes[60]
        self.t.translate([NOTE_OFF, 60, 0])
        self.t.translate([NOTE_ON, 62, 100])
        self.assertNotEqual(self.t.active_notes[62], first)

    def test_channels_do_come_back_once_others_have_been_used(self) -> None:
        for i, note in enumerate(range(60, 60 + len(self.t.member_channels))):
            self.t.translate([NOTE_ON, note, 100])
            self.t.translate([NOTE_OFF, note, 0])
        self.t.translate([NOTE_ON, 90, 100])
        self.assertIn(self.t.active_notes[90], self.t.member_channels)

    def test_retrigger_of_a_sounding_note_releases_the_old_channel(self) -> None:
        self.t.translate([NOTE_ON, 60, 100])
        out = self.t.translate([NOTE_ON, 60, 110])
        self.assertEqual(out[0][0] & 0xF0, NOTE_OFF)
        self.assertEqual(len(self.t.active_notes), 1)

    def test_exhaustion_steals_the_oldest_by_default(self) -> None:
        notes = list(range(60, 60 + len(self.t.member_channels)))
        for n in notes:
            self.t.translate([NOTE_ON, n, 100])
        out = self.t.translate([NOTE_ON, 99, 100])
        self.assertEqual(out[0], [NOTE_OFF | self.t.member_channels[0], notes[0], 0])
        self.assertNotIn(notes[0], self.t.active_notes)
        self.assertIn(99, self.t.active_notes)

    def test_steal_newest_takes_the_most_recent(self) -> None:
        t = ClassicToMpe(steal=STEAL_NEWEST)
        notes = list(range(60, 60 + len(t.member_channels)))
        for n in notes:
            t.translate([NOTE_ON, n, 100])
        t.translate([NOTE_ON, 99, 100])
        self.assertNotIn(notes[-1], t.active_notes)
        self.assertIn(notes[0], t.active_notes)

    def test_steal_never_drops_the_note_rather_than_hanging_one(self) -> None:
        t = ClassicToMpe(steal=STEAL_NEVER)
        notes = list(range(60, 60 + len(t.member_channels)))
        for n in notes:
            t.translate([NOTE_ON, n, 100])
        self.assertEqual(t.translate([NOTE_ON, 99, 100]), [])
        self.assertEqual(len(t.active_notes), len(t.member_channels))


class Expression(unittest.TestCase):
    def setUp(self) -> None:
        self.t = ClassicToMpe()

    def test_bend_goes_to_every_active_member_channel_and_never_the_master(self) -> None:
        for n in (60, 64):
            self.t.translate([NOTE_ON, n, 100])
        out = self.t.translate(bend_msg(0, 16383))
        self.assertEqual(len(out), 2)
        for m in out:
            self.assertNotEqual(m[0] & 0x0F, MASTER_CHANNEL)

    def test_bend_is_scaled_not_passed_through(self) -> None:
        self.t.translate([NOTE_ON, 60, 100])
        out = self.t.translate(bend_msg(0, 16383))
        self.assertLess(value14(out[0]), 16383)

    def test_a_note_arriving_mid_bend_inherits_it(self) -> None:
        """Otherwise the new note sounds at the wrong pitch until the next bend."""
        self.t.translate([NOTE_ON, 60, 100])
        self.t.translate(bend_msg(0, 16383))
        out = self.t.translate([NOTE_ON, 64, 100])
        kinds = [m[0] & 0xF0 for m in out]
        self.assertIn(PITCH_BEND, kinds)
        self.assertLess(kinds.index(PITCH_BEND), kinds.index(NOTE_ON))

    def test_bend_with_no_notes_emits_nothing(self) -> None:
        self.assertEqual(self.t.translate(bend_msg(0, 9000)), [])

    def test_channel_pressure_broadcasts_to_active_channels(self) -> None:
        for n in (60, 64):
            self.t.translate([NOTE_ON, n, 100])
        out = self.t.translate([CHANNEL_PRESSURE, 90])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(m[0] & 0xF0 == CHANNEL_PRESSURE for m in out))

    def test_poly_aftertouch_becomes_that_notes_channel_pressure(self) -> None:
        self.t.translate([NOTE_ON, 60, 100])
        self.t.translate([NOTE_ON, 64, 100])
        ch = self.t.active_notes[64]
        self.assertEqual(self.t.translate([POLY_AFTERTOUCH, 64, 77]),
                         [[CHANNEL_PRESSURE | ch, 77]])

    def test_mod_wheel_broadcasts(self) -> None:
        for n in (60, 64):
            self.t.translate([NOTE_ON, n, 100])
        out = self.t.translate([CONTROL_CHANGE, CC_MOD_WHEEL, 40])
        self.assertEqual(len(out), 2)

    def test_expression_broadcasts(self) -> None:
        self.t.translate([NOTE_ON, 60, 100])
        self.assertEqual(len(self.t.translate([CONTROL_CHANGE, CC_EXPRESSION, 40])), 1)


class SustainAndFiltering(unittest.TestCase):
    def setUp(self) -> None:
        self.t = ClassicToMpe()

    def test_sustain_goes_to_the_master_channel_untouched(self) -> None:
        """Surge holds the zone off the master and owns the deferral."""
        self.t.translate([NOTE_ON, 60, 100])
        self.assertEqual(self.t.translate([CONTROL_CHANGE, CC_SUSTAIN, 127]),
                         [[CONTROL_CHANGE | MASTER_CHANNEL, CC_SUSTAIN, 127]])

    def test_sustain_does_not_defer_note_off(self) -> None:
        """A second deferral mechanism racing Surge's is how notes stick."""
        self.t.translate([NOTE_ON, 60, 100])
        self.t.translate([CONTROL_CHANGE, CC_SUSTAIN, 127])
        out = self.t.translate([NOTE_OFF, 60, 0])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0] & 0xF0, NOTE_OFF)

    def test_program_change_is_dropped(self) -> None:
        self.assertEqual(self.t.translate([PROGRAM_CHANGE, 5]), [])

    def test_clock_and_transport_pass_through_untouched(self) -> None:
        for msg in ([0xF8], [0xFA], [0xFC]):
            self.assertEqual(self.t.translate(msg), [msg])

    def test_empty_message_is_ignored(self) -> None:
        self.assertEqual(self.t.translate([]), [])


class BendRangeDeclaration(unittest.TestCase):
    def test_rpn_zero_zero_updates_the_range_and_is_not_forwarded(self) -> None:
        t = ClassicToMpe()
        self.assertEqual(t.bend_semitones, 2.0)
        for msg in (
            [CONTROL_CHANGE, CC_RPN_MSB, 0],
            [CONTROL_CHANGE, CC_RPN_LSB, 0],
            [CONTROL_CHANGE, CC_DATA_ENTRY_MSB, 12],
        ):
            self.assertEqual(t.translate(msg), [], "RPN must be consumed")
        self.assertEqual(t.bend_semitones, 12.0)

    def test_a_declared_range_changes_the_scaling(self) -> None:
        t = ClassicToMpe()
        t.translate([NOTE_ON, 60, 100])
        narrow = value14(t.translate(bend_msg(0, 16383))[0])
        for msg in (
            [CONTROL_CHANGE, CC_RPN_MSB, 0],
            [CONTROL_CHANGE, CC_RPN_LSB, 0],
            [CONTROL_CHANGE, CC_DATA_ENTRY_MSB, 12],
        ):
            t.translate(msg)
        wide = value14(t.translate(bend_msg(0, 16383))[0])
        self.assertGreater(wide, narrow)

    def test_default_is_two_semitones_because_most_never_declare(self) -> None:
        self.assertEqual(ClassicToMpe().bend_semitones, 2.0)


class Safety(unittest.TestCase):
    def test_all_notes_off_releases_everything(self) -> None:
        t = ClassicToMpe()
        for n in (60, 64, 67):
            t.translate([NOTE_ON, n, 100])
        out = t.all_notes_off()
        self.assertEqual(len(out), 3)
        self.assertTrue(all(m[0] & 0xF0 == NOTE_OFF for m in out))
        self.assertEqual(t.active_notes, {})

    def test_all_notes_off_is_idempotent(self) -> None:
        t = ClassicToMpe()
        t.translate([NOTE_ON, 60, 100])
        t.all_notes_off()
        self.assertEqual(t.all_notes_off(), [])

    def test_cc123_panics_through_the_same_path(self) -> None:
        t = ClassicToMpe()
        t.translate([NOTE_ON, 60, 100])
        out = t.translate([CONTROL_CHANGE, 123, 0])
        self.assertEqual(len(out), 1)
        self.assertEqual(t.active_notes, {})

    def test_panic_leaves_the_channels_to_be_rewritten(self) -> None:
        """Panic clears OUR zone state but sends the member channels nothing,
        so Surge still holds the old bend (plan §7.9). The next note must write
        the channel rather than inherit it.

        This test previously asserted the opposite -- a bare note-on -- which is
        exactly the appliance failure measured 2026-10-10: one use of the wheel
        left channels bent and later notes played sharp with no bend in the
        stream.
        """
        t = ClassicToMpe()
        t.translate([NOTE_ON, 60, 100])
        t.translate(bend_msg(0, 16383))
        t.all_notes_off()
        out = t.translate([NOTE_ON, 62, 100])
        self.assertEqual(out[-1][0] & 0xF0, NOTE_ON, "note-on must come last")
        bends = [m for m in out if m[0] & 0xF0 == PITCH_BEND]
        self.assertEqual([value14(m) for m in bends], [BEND_CENTRE])


class ConfigurationMessage(unittest.TestCase):
    def test_mcm_is_rpn_6_on_the_master(self) -> None:
        msgs = mpe_configuration_message(15)
        self.assertEqual(msgs, [
            [CONTROL_CHANGE | MASTER_CHANNEL, CC_RPN_MSB, 6],
            [CONTROL_CHANGE | MASTER_CHANNEL, CC_RPN_LSB, 0],
            [CONTROL_CHANGE | MASTER_CHANNEL, CC_DATA_ENTRY_MSB, 15],
        ])

    def test_zero_members_disables_the_zone(self) -> None:
        self.assertEqual(mpe_configuration_message(0)[2][2], 0)

    def test_out_of_range_member_count_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            mpe_configuration_message(16)


class StaleChannelState(unittest.TestCase):
    """Surge keeps bend/pressure/timbre per channel for the life of the process
    and a new voice initialises from it (plan §7.9), so a reused channel must be
    written before its note-on. MEASURED on the appliance 2026-10-10."""

    def setUp(self) -> None:
        self.t = ClassicToMpe()
        # One member channel, so "the same channel comes back" is exact rather
        # than a function of allocation order.
        self.one = ClassicToMpe(member_channels=(1,))

    def _note(self, note: int) -> list[list[int]]:
        return self.t.translate([NOTE_ON, note, 100])

    def test_the_measured_field_case_a_reused_channel_is_recentred(self) -> None:
        """Bend, release while bent, wheel back with nothing sounding, replay.

        This is the appliance failure exactly: the wheel's return to centre
        reaches only sounding voices, so the released channel kept the bend and
        the next note there played sharp with no bend message in the stream.
        """
        t = self.one
        t.translate([NOTE_ON, 69, 100])
        t.translate(bend_msg(0, 16383))
        t.translate([NOTE_OFF, 69, 0])
        self.assertEqual(t.translate(bend_msg(0, BEND_CENTRE)), [],
                         "nothing is sounding, so the centring reaches no channel")
        out = t.translate([NOTE_ON, 69, 100])
        bends = [m for m in out if m[0] & 0xF0 == PITCH_BEND]
        self.assertEqual(len(bends), 1, f"no re-centring bend: {out}")
        self.assertEqual(value14(bends[0]), BEND_CENTRE)
        self.assertEqual(out[-1][0] & 0xF0, NOTE_ON)

    def test_an_unprimed_channel_is_written_even_at_rest(self) -> None:
        """Unknown is not the same as at rest: Surge may hold anything."""
        out = self._note(60)
        kinds = [m[0] & 0xF0 for m in out]
        self.assertIn(PITCH_BEND, kinds)
        self.assertIn(CHANNEL_PRESSURE, kinds)
        self.assertEqual(kinds[-1], NOTE_ON, "state must precede the note-on")

    def test_priming_precedes_the_note_on(self) -> None:
        """Surge reads the channel at voice creation; after is a slide."""
        self.t.translate([NOTE_ON, 60, 100])
        self.t.translate(bend_msg(0, 10000))
        self.t.translate([NOTE_OFF, 60, 0])
        out = self._note(64)
        self.assertEqual(out[-1][0] & 0xF0, NOTE_ON)
        self.assertTrue(all(m[0] & 0xF0 != NOTE_ON for m in out[:-1]))

    def test_an_already_matching_channel_costs_nothing(self) -> None:
        """Priming must not add a message per note once a channel is known."""
        t = self.one
        t.translate([NOTE_ON, 60, 100])
        t.translate([NOTE_OFF, 60, 0])
        out = t.translate([NOTE_ON, 60, 100])
        self.assertEqual(len(out), 1, f"expected the note-on alone: {out}")
        self.assertEqual(out[0][0] & 0xF0, NOTE_ON)

    def test_stale_pressure_is_cleared_on_reuse(self) -> None:
        """Same defect, different message: pressure is zone-wide and sticks."""
        t = self.one
        t.translate([NOTE_ON, 60, 100])
        t.translate([CHANNEL_PRESSURE, 100])
        t.translate([NOTE_OFF, 60, 0])
        t.translate([CHANNEL_PRESSURE, 0])  # reaches nothing
        out = t.translate([NOTE_ON, 60, 100])
        pressures = [m for m in out if m[0] & 0xF0 == CHANNEL_PRESSURE]
        self.assertEqual([m[1] for m in pressures], [0], f"stale pressure: {out}")

    def test_stale_broadcast_cc_is_cleared_on_reuse(self) -> None:
        t = self.one
        t.translate([NOTE_ON, 60, 100])
        t.translate([CONTROL_CHANGE, CC_MOD_WHEEL, 90])
        t.translate([NOTE_OFF, 60, 0])
        t.translate([CONTROL_CHANGE, CC_MOD_WHEEL, 0])
        out = t.translate([NOTE_ON, 60, 100])
        mods = [m for m in out if m[0] & 0xF0 == CONTROL_CHANGE and m[1] == CC_MOD_WHEEL]
        self.assertEqual([m[2] for m in mods], [0], f"stale mod wheel: {out}")

    def test_poly_aftertouch_does_not_become_the_zone_pressure(self) -> None:
        """It is per note by definition; treating it as zone state would leak
        one key's pressure onto every later note."""
        self.t.translate([NOTE_ON, 60, 100])
        self.t.translate([POLY_AFTERTOUCH, 60, 120])
        self.t.translate([NOTE_OFF, 60, 0])
        out = self.t.translate([NOTE_ON, 72, 100])
        pressures = [m[1] for m in out if m[0] & 0xF0 == CHANNEL_PRESSURE]
        self.assertEqual(pressures, [0], f"zone pressure leaked: {out}")

    def test_zone_reset_writes_every_member_channel(self) -> None:
        """What cured the appliance by hand: bend, pressure and timbre on all."""
        out = self.t.zone_reset()
        for channel in self.t.member_channels:
            mine = [m for m in out if m[0] & 0x0F == channel]
            kinds = {m[0] & 0xF0 for m in mine}
            self.assertEqual(kinds, {PITCH_BEND, CHANNEL_PRESSURE, CONTROL_CHANGE})
        self.assertEqual(
            self.t.translate([NOTE_ON, 60, 100]), [[NOTE_ON | self.t.member_channels[0], 60, 100]],
            "after a reset the channels are known, so a note costs one message",
        )


class RpnNull(unittest.TestCase):
    def test_rpn_null_closes_the_parameter(self) -> None:
        """Without this, any later CC6 -- a common generic knob assignment --
        is read as a bend-range declaration and rescales every bend."""
        t = ClassicToMpe()
        for msg in (
            [CONTROL_CHANGE, CC_RPN_MSB, 0],
            [CONTROL_CHANGE, CC_RPN_LSB, 0],
            [CONTROL_CHANGE, CC_DATA_ENTRY_MSB, 2],
        ):
            t.translate(msg)
        self.assertEqual(t.bend_semitones, 2.0)
        for msg in (
            [CONTROL_CHANGE, CC_RPN_MSB, 127],
            [CONTROL_CHANGE, CC_RPN_LSB, 127],
        ):
            t.translate(msg)
        t.translate([CONTROL_CHANGE, CC_DATA_ENTRY_MSB, 48])
        self.assertEqual(t.bend_semitones, 2.0, "a stray CC6 rewrote the bend range")


if __name__ == "__main__":
    unittest.main()
