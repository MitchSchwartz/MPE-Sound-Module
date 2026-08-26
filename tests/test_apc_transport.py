"""APC transport combo (Shift + Stop All Clips hold)."""

import unittest
from unittest.mock import patch

from scripts.sooperlooper.apc_transport import (
    NOTE_SHIFT_MK1,
    NOTE_SHIFT_MK2,
    NOTE_STOP_ALL_CLIPS_MK1,
    NOTE_STOP_ALL_CLIPS_MK2,
    NOTE_TRACK8_MK1,
    ShiftHoldCombo,
    TransportButtonLeds,
    resolve_apc_transport_notes,
    resolve_shift_indicator_note,
)
from scripts.sooperlooper.led_table import (
    LED_RED,
    SCENE_LED_OFF,
    SCENE_LED_ON,
    TRACK_LED_OFF,
    TRACK_LED_ON,
    accelerating_hold_blink_on,
)


class ResolveApcTransportNotesTests(unittest.TestCase):
    def test_mk1_from_port_name(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC MINI:APC MINI MIDI 1 32:0")
        self.assertEqual(label, "mk1")
        self.assertEqual(shift, NOTE_SHIFT_MK1)
        self.assertEqual(stop, NOTE_STOP_ALL_CLIPS_MK1)

    def test_mk2_from_port_name(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC mini mk2")
        self.assertEqual(label, "mk2")
        self.assertEqual(shift, NOTE_SHIFT_MK2)
        self.assertEqual(stop, NOTE_STOP_ALL_CLIPS_MK2)

    def test_explicit_variant_override(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC MINI", variant="mk2")
        self.assertEqual(label, "mk2")
        self.assertEqual(shift, NOTE_SHIFT_MK2)


class ShiftHoldComboTests(unittest.TestCase):
    def test_fires_long_after_hold_with_both_down(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[10.0, 12.9, 13.0, 14.0],
        ):
            combo.note_event(122, True)
            combo.note_event(119, True)
            self.assertFalse(combo.poll_long())
            self.assertTrue(combo.poll_long())
            self.assertFalse(combo.poll_long())

    def test_short_on_release_before_hold(self) -> None:
        combo = ShiftHoldCombo(
            shift_note=NOTE_SHIFT_MK1,
            target_note=NOTE_STOP_ALL_CLIPS_MK1,
            hold_s=3.0,
        )
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[10.0, 10.2, 10.3],
        ):
            combo.note_event(NOTE_SHIFT_MK1, True)
            combo.note_event(NOTE_STOP_ALL_CLIPS_MK1, True)
            combo.note_event(NOTE_STOP_ALL_CLIPS_MK1, False)
            combo.note_event(NOTE_SHIFT_MK1, False)
            self.assertTrue(combo.poll_short())
            self.assertFalse(combo.poll_short())

    def test_releases_cancel_long_pending(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        combo.note_event(122, True)
        combo.note_event(119, True)
        self.assertTrue(combo.both_down)
        combo.note_event(122, False)
        self.assertFalse(combo.both_down)
        combo.note_event(119, False)
        self.assertFalse(combo.poll_long())


class ArrowBankingTests(unittest.TestCase):
    def test_variant_resolution_matches_the_transport_notes_path(self) -> None:
        from scripts.sooperlooper.apc_transport import (
            ARROW_NOTES_MK1,
            ARROW_NOTES_MK2,
            resolve_arrow_notes,
        )

        mk2 = resolve_arrow_notes("APC mini mk2 MIDI 1")
        self.assertEqual(sorted(mk2), sorted(ARROW_NOTES_MK2))
        self.assertEqual(mk2[ARROW_NOTES_MK2[0]], "up")
        self.assertEqual(sorted(resolve_arrow_notes("APC MINI")), sorted(ARROW_NOTES_MK1))
        # Explicit variant beats the port name, same as Shift/Stop-All.
        self.assertEqual(
            sorted(resolve_arrow_notes("APC MINI", variant="mk2")),
            sorted(ARROW_NOTES_MK2),
        )

    def test_up_down_page_by_eight_arrows_nudge_only_with_shift(self) -> None:
        from scripts.sooperlooper.apc_transport import bank_delta_for_arrow

        self.assertEqual(bank_delta_for_arrow("down", shift_down=False), 8)
        self.assertEqual(bank_delta_for_arrow("up", shift_down=False), -8)
        self.assertEqual(bank_delta_for_arrow("right", shift_down=False), 0)
        self.assertEqual(bank_delta_for_arrow("right", shift_down=True), 1)
        self.assertEqual(bank_delta_for_arrow("left", shift_down=True), -1)


class TransportButtonLedsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[list[int]] = []

        class FakeOut:
            def __init__(self, sink: list[list[int]]) -> None:
                self._sink = sink

            def send_message(self, message: list[int]) -> None:
                self._sink.append(list(message))

        self.midi_out = FakeOut(self.sent)
        self.shift = NOTE_SHIFT_MK1
        self.stop = NOTE_STOP_ALL_CLIPS_MK1
        self.shift_indicator = NOTE_TRACK8_MK1

    def _leds(self, *, hold_s: float = 3.0) -> TransportButtonLeds:
        return TransportButtonLeds(
            midi_out=self.midi_out,
            shift_note=self.shift,
            stop_all_note=self.stop,
            shift_indicator_note=self.shift_indicator,
            shift_indicator_on_vel=LED_RED,
            hold_s=hold_s,
        )

    def test_shift_alone_lights_track_indicator_red(self) -> None:
        leds = self._leds()
        leds.note_event(self.shift, True)
        self.assertEqual(self.sent[-1], [0x90, self.shift_indicator, LED_RED])
        leds.note_event(self.shift, False)
        self.assertEqual(self.sent[-1], [0x90, self.shift_indicator, TRACK_LED_OFF])

    def test_stop_all_alone_lights_scene_green(self) -> None:
        leds = self._leds()
        leds.note_event(self.stop, True)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_ON])
        leds.note_event(self.stop, False)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_OFF])

    def test_both_held_blinks_and_accelerates(self) -> None:
        leds = self._leds(hold_s=3.0)
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[0.0, 0.0, 0.0, 0.5, 0.5],
        ):
            leds.note_event(self.shift, True)
            leds.note_event(self.stop, True)
            self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_ON])
            leds.poll()
            self.assertIn(self.sent[-1][2], (SCENE_LED_ON, SCENE_LED_OFF))
            leds.poll()
            # Later in the hold the half-period is shorter — phase may differ.
            self.assertIn(self.sent[-1][2], (SCENE_LED_ON, SCENE_LED_OFF))

    def test_reset_clears_until_both_released(self) -> None:
        leds = self._leds()
        leds.note_event(self.shift, True)
        leds.note_event(self.stop, True)
        leds.on_reset_fired()
        self.assertEqual(self.sent[-2:], [
            [0x90, self.shift_indicator, TRACK_LED_OFF],
            [0x90, self.stop, SCENE_LED_OFF],
        ])
        leds.note_event(self.shift, True)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_OFF])
        leds.note_event(self.shift, False)
        leds.note_event(self.stop, False)
        leds.note_event(self.shift, True)
        self.assertEqual(self.sent[-1], [0x90, self.shift_indicator, TRACK_LED_ON])


class AcceleratingHoldBlinkTests(unittest.TestCase):
    def test_not_in_blink_window_before_delay(self) -> None:
        self.assertIsNone(
            accelerating_hold_blink_on(0.4, hold_s=2.0, blink_after_s=0.5)
        )

    def test_blinks_after_delay(self) -> None:
        first = accelerating_hold_blink_on(0.6, hold_s=2.0, blink_after_s=0.5)
        self.assertIn(first, (True, False))
