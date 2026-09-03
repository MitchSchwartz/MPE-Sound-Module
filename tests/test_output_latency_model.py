"""The measured latency model behind the looper-grid MIDI offset.

Anchored on docs/measurements/midi-audio-latency-phase{1,2}-2026-09-02.md. If a
number here changes, a measurement has to change with it -- these are not
preferences.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patch_browser import midi_sync


class SurgeMidiLegTests(unittest.TestCase):
    def test_reproduces_both_measured_periods_within_three_frames(self):
        # MEASURED n=30 each: 96 -> 159 frames, 192 -> 249 frames.
        self.assertAlmostEqual(midi_sync.surge_midi_leg_frames(96), 159, delta=3)
        self.assertAlmostEqual(midi_sync.surge_midi_leg_frames(192), 249, delta=3)

    def test_scales_with_period_because_surge_waits_for_the_next_callback(self):
        a = midi_sync.surge_midi_leg_frames(96)
        b = midi_sync.surge_midi_leg_frames(192)
        self.assertEqual(b - a, 96)

    def test_nonsense_period_yields_zero_rather_than_a_negative_offset(self):
        self.assertEqual(midi_sync.surge_midi_leg_frames(0), 0)
        self.assertEqual(midi_sync.surge_midi_leg_frames(-1), 0)


class OutputLatencyTableTests(unittest.TestCase):
    def test_measured_devices_are_present_with_their_measured_values(self):
        table = midi_sync._load_output_latency_table()
        self.assertEqual(table["usb:2972:0051"], 64)   # FiiO KA1
        self.assertEqual(table["usb:1235:8212"], 98)   # Scarlett 4i4

    def test_comments_and_blank_lines_are_not_entries(self):
        table = midi_sync._load_output_latency_table()
        self.assertTrue(all(k.startswith("usb:") for k in table))

    def test_unmeasured_device_contributes_zero_and_is_never_guessed(self):
        # A device nobody has put on a loopback must contribute nothing, not a
        # value borrowed from a device that merely looks similar.
        with mock.patch.object(midi_sync, "_running_card_key", return_value="usb:dead:beef"):
            self.assertEqual(midi_sync.output_hardware_latency_frames(), 0)

    def test_unresolvable_card_contributes_zero(self):
        with mock.patch.object(midi_sync, "_running_card_key", return_value=None):
            self.assertEqual(midi_sync.output_hardware_latency_frames(), 0)


class TotalOutputLatencyTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for k in ("MPE_MIDI_OUTPUT_OFFSET_MS", "MPE_MIDI_OUTPUT_OFFSET_AUTO"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_sums_all_three_legs_on_the_ka1_at_the_shipped_graph(self):
        with mock.patch.object(midi_sync, "_running_graph_params", return_value=(96, 2, 48000)), \
             mock.patch.object(midi_sync, "output_hardware_latency_frames", return_value=64):
            # 156 (Surge) + 192 (declared) + 64 (KA1) = 412 frames -- what the
            # PLAYER HEARS. Display only; deliberately not the offset.
            self.assertAlmostEqual(midi_sync.total_output_latency_ms(), 412 * 1000 / 48000, places=6)

    def test_tracks_the_period_the_server_is_actually_running(self):
        # The ladder climbs when a DAC cannot sustain the configured period, and
        # compensating for the requested one is the error this guards.
        with mock.patch.object(midi_sync, "_running_graph_params", return_value=(256, 2, 48000)), \
             mock.patch.object(midi_sync, "output_hardware_latency_frames", return_value=0):
            self.assertAlmostEqual(
                midi_sync.total_output_latency_ms(), (316 + 512) * 1000 / 48000, places=6
            )

    def test_no_graph_and_no_env_never_yields_a_negative_or_absurd_offset(self):
        with mock.patch.object(midi_sync, "_running_graph_params", return_value=None), \
             mock.patch.object(midi_sync, "output_hardware_latency_frames", return_value=0):
            value = midi_sync.total_output_latency_ms()
        self.assertGreaterEqual(value, 0.0)
        self.assertLess(value, 200.0)


class LooperAlignmentTests(unittest.TestCase):
    """The offset owns ONE leg. Both other legs sit after the looper's tap."""

    def test_offset_is_the_surge_leg_alone(self):
        with mock.patch.object(midi_sync, "_running_graph_params", return_value=(96, 2, 48000)):
            self.assertAlmostEqual(
                midi_sync.looper_alignment_latency_ms(), 156 * 1000 / 48000, places=6
            )

    def test_offset_excludes_the_playback_ringbuffer(self):
        """period x periods is Surge's port -> the converter, downstream of the
        looper input. It was the ENTIRE model until 2026-09-02 and was the wrong
        leg: right magnitude, wrong path."""
        with mock.patch.object(midi_sync, "_running_graph_params", return_value=(96, 2, 48000)):
            offset = midi_sync.looper_alignment_latency_ms()
            ringbuffer = midi_sync.buffer_latency_ms(96, 48000, 2)
        self.assertLess(offset, ringbuffer)

    def test_offset_is_unaffected_by_the_dac(self):
        """The recorded signal never reaches the converter. A DAC term in the
        offset compensates for a delay that is not in this path -- and one that
        cancels at the ear anyway, since loop playback traverses it too."""
        with mock.patch.object(midi_sync, "_running_graph_params", return_value=(96, 2, 48000)):
            with mock.patch.object(midi_sync, "output_hardware_latency_frames", return_value=0):
                without = midi_sync.looper_alignment_latency_ms()
            with mock.patch.object(midi_sync, "output_hardware_latency_frames", return_value=500):
                with_dac = midi_sync.looper_alignment_latency_ms()
        self.assertEqual(without, with_dac)

    def test_offset_is_strictly_smaller_than_what_the_player_hears(self):
        with mock.patch.object(midi_sync, "_running_graph_params", return_value=(96, 2, 48000)), \
             mock.patch.object(midi_sync, "output_hardware_latency_frames", return_value=98):
            self.assertLess(
                midi_sync.looper_alignment_latency_ms(), midi_sync.total_output_latency_ms()
            )


class ResolveOffsetTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for k in ("MPE_MIDI_OUTPUT_OFFSET_MS", "MPE_MIDI_OUTPUT_OFFSET_AUTO"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_auto_offset_is_the_negated_looper_alignment_not_the_path_to_the_ear(self):
        with mock.patch.object(midi_sync, "looper_alignment_latency_ms", return_value=3.25), \
             mock.patch.object(midi_sync, "total_output_latency_ms", return_value=9.29):
            self.assertAlmostEqual(midi_sync.resolve_output_offset_ms(), -3.25)

    def test_explicit_override_still_wins(self):
        os.environ["MPE_MIDI_OUTPUT_OFFSET_MS"] = "-12.5"
        self.assertAlmostEqual(midi_sync.resolve_output_offset_ms(), -12.5)

    def test_auto_can_be_switched_off(self):
        os.environ["MPE_MIDI_OUTPUT_OFFSET_AUTO"] = "0"
        self.assertEqual(midi_sync.resolve_output_offset_ms(), 0.0)


if __name__ == "__main__":
    unittest.main()
