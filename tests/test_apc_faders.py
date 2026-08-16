import unittest

from apc_faders import (
    MASTER,
    NUM_LOOP_FADERS,
    fader_for_cc,
    is_control_change,
    resolve_fader_ccs,
)


class ResolveFaderCcs(unittest.TestCase):
    def test_explicit_variant_wins_over_port_name(self):
        _ccs, _master, label = resolve_fader_ccs("APC MINI mk2", variant="mk1")
        self.assertEqual(label, "mk1")

    def test_port_name_detects_mk2(self):
        _ccs, _master, label = resolve_fader_ccs("APC mini mk2 MIDI 1")
        self.assertEqual(label, "mk2")

    def test_unknown_port_falls_back_to_mk1(self):
        _ccs, _master, label = resolve_fader_ccs("some other surface")
        self.assertEqual(label, "mk1")

    def test_eight_contiguous_loop_faders_plus_a_distinct_master(self):
        for variant in ("mk1", "mk2"):
            ccs, master, _ = resolve_fader_ccs("", variant=variant)
            self.assertEqual(len(ccs), NUM_LOOP_FADERS, variant)
            self.assertEqual(len(set(ccs)), NUM_LOOP_FADERS, variant)
            self.assertNotIn(master, ccs, variant)
            self.assertEqual(list(ccs), sorted(ccs), variant)


class FaderForCc(unittest.TestCase):
    def setUp(self):
        self.ccs, self.master, _ = resolve_fader_ccs("", variant="mk2")

    def _lookup(self, cc):
        return fader_for_cc(cc, loop_fader_ccs=self.ccs, master_cc=self.master)

    def test_each_loop_cc_maps_to_its_index(self):
        for index, cc in enumerate(self.ccs):
            self.assertEqual(self._lookup(cc), index)

    def test_master_cc_maps_to_master_not_to_index_eight(self):
        self.assertEqual(self._lookup(self.master), MASTER)

    def test_unrelated_cc_is_not_a_fader(self):
        self.assertIsNone(self._lookup(7))
        self.assertIsNone(self._lookup(self.master + 1))


class IsControlChange(unittest.TestCase):
    def test_recognises_cc_on_every_channel(self):
        for channel in range(16):
            self.assertTrue(is_control_change(0xB0 | channel))

    def test_rejects_note_messages(self):
        self.assertFalse(is_control_change(0x90))
        self.assertFalse(is_control_change(0x80))


if __name__ == "__main__":
    unittest.main()
