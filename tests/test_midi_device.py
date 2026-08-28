"""Device classification. Pure; no ports, no hardware."""

import unittest

from scripts.midi_device import (
    KIND_CLASSIC,
    KIND_MPE,
    REASON_DEFAULT,
    REASON_KNOWN_NAME,
    REASON_KNOWN_USB,
    REASON_MCM,
    REASON_OVERRIDE,
    Classification,
    MpeConfigDetector,
    classify_port,
    name_looks_mpe,
)
from scripts.midi_translate import mpe_configuration_message


class DetectsMcm(unittest.TestCase):
    def setUp(self) -> None:
        self.d = MpeConfigDetector()

    def test_a_real_mcm_is_detected(self) -> None:
        """Fed the exact bytes our own emitter produces."""
        results = [self.d.feed(m) for m in mpe_configuration_message(15)]
        self.assertEqual(results, [False, False, True])
        self.assertTrue(self.d.seen)
        self.assertEqual(self.d.member_channels, 15)

    def test_upper_zone_on_channel_16(self) -> None:
        for m in mpe_configuration_message(7, master_channel=15):
            self.d.feed(m)
        self.assertTrue(self.d.seen)
        self.assertEqual(self.d.member_channels, 7)

    def test_zone_disabling_mcm_is_not_evidence_of_mpe(self) -> None:
        """mm=0 says 'not MPE' — reading it as MPE inverts the message."""
        for m in mpe_configuration_message(0):
            self.d.feed(m)
        self.assertEqual(self.d.member_channels, 0)
        self.assertFalse(self.d.seen)

    def test_rpn_zero_zero_is_not_an_mcm(self) -> None:
        """Bend sensitivity is RPN 0/0; MCM is RPN 6. Confusing them is the bug."""
        for m in ([0xB0, 101, 0], [0xB0, 100, 0], [0xB0, 6, 12]):
            self.assertFalse(self.d.feed(m))
        self.assertFalse(self.d.seen)

    def test_mcm_on_a_non_master_channel_is_invalid(self) -> None:
        for m in ([0xB5, 101, 6], [0xB5, 100, 0], [0xB5, 6, 15]):
            self.assertFalse(self.d.feed(m))
        self.assertFalse(self.d.seen)

    def test_rpn_state_is_per_channel(self) -> None:
        """A half-selected RPN elsewhere must not complete this one."""
        self.d.feed([0xB0, 101, 6])
        self.d.feed([0xB5, 100, 0])          # different channel
        self.assertFalse(self.d.feed([0xB0, 6, 15]))
        self.assertFalse(self.d.seen)

    def test_notes_and_bend_are_ignored(self) -> None:
        for m in ([0x90, 60, 100], [0x80, 60, 0], [0xE0, 0, 64], [0xD0, 90]):
            self.assertFalse(self.d.feed(m))

    def test_short_and_empty_messages_do_not_crash(self) -> None:
        for m in ([], [0xB0], [0xB0, 101]):
            self.assertFalse(self.d.feed(m))

    def test_member_count_above_15_is_rejected(self) -> None:
        for m in ([0xB0, 101, 6], [0xB0, 100, 0], [0xB0, 6, 99]):
            self.d.feed(m)
        self.assertFalse(self.d.seen)


class ClassifiesPorts(unittest.TestCase):
    def test_mcm_wins_over_everything(self) -> None:
        d = MpeConfigDetector()
        for m in mpe_configuration_message(15):
            d.feed(m)
        c = classify_port("Generic USB Keyboard", detector=d)
        self.assertEqual((c.kind, c.reason), (KIND_MPE, REASON_MCM))
        self.assertEqual(c.member_channels, 15)

    def test_known_name_is_mpe_without_an_mcm(self) -> None:
        for name in ("LUMI Keys Block", "Seaboard RISE 49", "ROLI Piano",
                     "LinnStrument MIDI"):
            c = classify_port(name)
            self.assertEqual(c.kind, KIND_MPE, name)
            self.assertEqual(c.reason, REASON_KNOWN_NAME)

    def test_known_usb_vendor_is_mpe(self) -> None:
        c = classify_port("Unnamed Port", usb_vendors={"2AF4"})
        self.assertEqual((c.kind, c.reason), (KIND_MPE, REASON_KNOWN_USB))

    def test_unknown_device_defaults_to_classic(self) -> None:
        c = classify_port("Some MIDI Keyboard 61")
        self.assertEqual((c.kind, c.reason), (KIND_CLASSIC, REASON_DEFAULT))
        self.assertFalse(c.is_mpe)

    def test_default_is_classic_because_the_errors_are_asymmetric(self) -> None:
        """Classic-as-MPE bends 24x too wide; MPE-as-classic only loses nuance."""
        self.assertEqual(classify_port("").kind, KIND_CLASSIC)

    def test_override_beats_mcm(self) -> None:
        d = MpeConfigDetector()
        for m in mpe_configuration_message(15):
            d.feed(m)
        c = classify_port("LUMI", detector=d, override=KIND_CLASSIC)
        self.assertEqual((c.kind, c.reason), (KIND_CLASSIC, REASON_OVERRIDE))

    def test_a_nonsense_override_is_ignored(self) -> None:
        c = classify_port("Some Keyboard", override="banana")
        self.assertEqual(c.kind, KIND_CLASSIC)
        self.assertEqual(c.reason, REASON_DEFAULT)

    def test_every_result_carries_a_reason_for_the_ui(self) -> None:
        """A classification you cannot see is one you cannot debug."""
        for c in (classify_port("LUMI"), classify_port("Nord"),
                  classify_port("x", usb_vendors={"2af4"})):
            self.assertTrue(c.reason)
            self.assertIsInstance(c, Classification)

    def test_name_matching_is_case_insensitive(self) -> None:
        self.assertTrue(name_looks_mpe("lumi keys"))
        self.assertTrue(name_looks_mpe("LUMI KEYS"))
        self.assertFalse(name_looks_mpe("Casio CTK"))


if __name__ == "__main__":
    unittest.main()
