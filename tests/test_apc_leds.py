"""The mk2 speaks a different LED protocol; the mk1 must not notice this module."""

from __future__ import annotations

from tests import conftest  # noqa: F401

import unittest

from scripts.sooperlooper import apc_leds
from scripts.sooperlooper.apc_leds import translate
from scripts.sooperlooper.led_table import (
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
)


class Mk1IsUntouched(unittest.TestCase):
    """Identity for the mk1, so this module cannot regress a working surface."""

    def test_every_colour_passes_through_byte_for_byte(self) -> None:
        for colour in (LED_OFF, LED_GREEN, LED_GREEN_BLINK, LED_RED,
                       LED_RED_BLINK, LED_YELLOW, LED_YELLOW_BLINK):
            with self.subTest(colour=colour):
                msg = [0x90, 0x11, colour]
                self.assertEqual(translate(msg, "mk1"), msg)

    def test_unknown_model_is_also_identity(self) -> None:
        msg = [0x90, 0x11, LED_GREEN]
        self.assertEqual(translate(msg, None), msg)
        self.assertEqual(translate(msg, "env"), msg)


class Mk2PadEncoding(unittest.TestCase):
    def test_brightness_is_not_the_default_ten_percent(self) -> None:
        """0x90 is 10% on the mk2 — the whole of 'pads barely light up'."""
        status, _note, _vel = translate([0x90, 0x00, LED_GREEN], "mk2")
        self.assertNotEqual(status, 0x90)
        self.assertEqual(status, apc_leds.MK2_SOLID)

    def test_colours_land_on_the_documented_palette_indices(self) -> None:
        for colour, palette in (
            (LED_GREEN, apc_leds.MK2_GREEN),
            (LED_RED, apc_leds.MK2_RED),
            (LED_YELLOW, apc_leds.MK2_YELLOW),
            (LED_OFF, apc_leds.MK2_BLACK),
        ):
            with self.subTest(colour=colour):
                self.assertEqual(
                    translate([0x90, 0x07, colour], "mk2")[2], palette
                )

    def test_blink_stays_a_blink(self) -> None:
        """Blink means 'queued, lands next bar'. Collapsing it to solid would
        make an armed pad indistinguishable from a landed one."""
        for colour in (LED_GREEN_BLINK, LED_RED_BLINK, LED_YELLOW_BLINK):
            with self.subTest(colour=colour):
                self.assertEqual(
                    translate([0x90, 0x07, colour], "mk2")[0], apc_leds.MK2_BLINK
                )

    def test_blink_and_solid_share_a_hue(self) -> None:
        for solid, blink in ((LED_GREEN, LED_GREEN_BLINK),
                             (LED_RED, LED_RED_BLINK),
                             (LED_YELLOW, LED_YELLOW_BLINK)):
            with self.subTest(solid=solid):
                self.assertEqual(
                    translate([0x90, 0x07, solid], "mk2")[2],
                    translate([0x90, 0x07, blink], "mk2")[2],
                )

    def test_the_note_is_never_rewritten(self) -> None:
        for note in (0x00, 0x1F, 0x3F):
            with self.subTest(note=note):
                self.assertEqual(translate([0x90, note, LED_RED], "mk2")[1], note)


class OnlyGridPadsAreTranslated(unittest.TestCase):
    """Button LEDs already agree across models — 0x90, 0=off/1=on/2=blink."""

    def test_button_notes_pass_through(self) -> None:
        for note in (0x64, 0x6B, 0x70, 0x77):
            with self.subTest(note=hex(note)):
                msg = [0x90, note, 1]
                self.assertEqual(translate(msg, "mk2"), msg)

    def test_non_note_on_passes_through(self) -> None:
        for msg in ([0xB0, 0x30, 0x40], [0x80, 0x01, 0x00], [0xF8]):
            with self.subTest(msg=msg):
                self.assertEqual(translate(list(msg), "mk2"), list(msg))

    def test_unmapped_velocity_is_left_alone(self) -> None:
        msg = [0x90, 0x02, 99]
        self.assertEqual(translate(msg, "mk2"), msg)

    def test_malformed_messages_do_not_raise(self) -> None:
        for msg in ([], [0x90], [0x90, 0x01], None):
            with self.subTest(msg=msg):
                self.assertEqual(translate(msg, "mk2"), msg)


class ThroughThePacer(unittest.TestCase):
    """The pacer is the seam, so the encoding must survive the queue."""

    def _pacer(self, label):
        from scripts.sooperlooper.apc_link import PacedMidiOut

        class Sink:
            def __init__(self): self.sent = []
            def send_message(self, m): self.sent.append(list(m))

        sink = Sink()
        clock = [0.0]
        out = PacedMidiOut(sink, gap_s=0.0, now=lambda: clock[0])
        out.apc_label = label
        return out, sink

    def test_mk2_pad_is_encoded_on_the_way_out(self) -> None:
        out, sink = self._pacer("mk2")
        out.send_message([0x90, 0x00, LED_GREEN])
        out.pump()
        self.assertEqual(sink.sent, [[apc_leds.MK2_SOLID, 0x00, apc_leds.MK2_GREEN]])

    def test_mk1_pad_is_unchanged_on_the_way_out(self) -> None:
        out, sink = self._pacer("mk1")
        out.send_message([0x90, 0x00, LED_GREEN])
        out.pump()
        self.assertEqual(sink.sent, [[0x90, 0x00, LED_GREEN]])

    def test_relabelling_drops_a_backlog_encoded_for_the_old_model(self) -> None:
        out, sink = self._pacer("mk1")
        out.send_message([0x90, 0x00, LED_GREEN])
        out.apc_label = "mk2"
        out.pump()
        self.assertEqual(sink.sent, [])


if __name__ == "__main__":
    unittest.main()
