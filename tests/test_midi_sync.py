"""Tests for looper-grid MIDI timing (offset + quantize)."""

import os
import unittest

from patch_browser.midi_sync import (
    buffer_latency_ms,
    next_grid_monotonic,
    parse_quantize_grid_ticks,
    plan_fire_at,
    resolve_output_offset_ms,
)


class TestMidiSyncConfig(unittest.TestCase):
    def test_parse_quantize(self) -> None:
        self.assertEqual(parse_quantize_grid_ticks("16th"), 6)
        self.assertEqual(parse_quantize_grid_ticks("off"), 0)
        self.assertEqual(parse_quantize_grid_ticks("beat"), 24)

    def test_parse_quantize_triplet_modifier(self) -> None:
        self.assertEqual(parse_quantize_grid_ticks("8th", triplet=True), 8)
        self.assertEqual(parse_quantize_grid_ticks("16th", triplet=True), 4)
        self.assertEqual(parse_quantize_grid_ticks("triplet"), 8)

    def test_buffer_latency(self) -> None:
        # periods is explicit here; the graph term is covered below.
        self.assertAlmostEqual(buffer_latency_ms(48000, 48000, periods=1), 1000.0)
        self.assertAlmostEqual(
            buffer_latency_ms(1024, 48000, periods=1), 1000.0 * 1024 / 48000
        )

    def test_buffer_latency_counts_all_jack_periods(self) -> None:
        """Real output latency is period × periods — one period under-reports by 3×."""
        self.assertAlmostEqual(
            buffer_latency_ms(256, 48000, periods=3), 1000.0 * 256 * 3 / 48000
        )

    def test_auto_offset_uses_jack_period_and_periods(self) -> None:
        env = os.environ.copy()
        try:
            os.environ.pop("MPE_MIDI_OUTPUT_OFFSET_MS", None)
            os.environ["MPE_MIDI_OUTPUT_OFFSET_AUTO"] = "1"
            os.environ["MPE_JACK_BUFFER"] = "256"
            os.environ["MPE_JACK_PERIODS"] = "3"
            os.environ["MPE_SURGE_SAMPLE_RATE"] = "48000"
            # The retired Surge key must not win over the live graph period.
            os.environ["MPE_SURGE_BUFFER_SIZE"] = "1024"
            self.assertAlmostEqual(
                resolve_output_offset_ms(), -1000.0 * 256 * 3 / 48000
            )
        finally:
            os.environ.clear()
            os.environ.update(env)


class TestGridTiming(unittest.TestCase):
    def test_next_grid_on_beat(self) -> None:
        snap = {"bpm_raw": 120.0, "transport_ticks": 24, "ticks_in_beat": 0}
        self.assertAlmostEqual(next_grid_monotonic(10.0, snap, 6), 10.0)

    def test_next_grid_waits_for_16th(self) -> None:
        snap = {"bpm_raw": 120.0, "transport_ticks": 1, "ticks_in_beat": 1}
        expected = 10.0 + 5 * (0.5 / 24)
        self.assertAlmostEqual(next_grid_monotonic(10.0, snap, 6), expected, places=6)

    def test_next_grid_waits_for_triplet(self) -> None:
        snap = {"bpm_raw": 120.0, "transport_ticks": 1, "ticks_in_beat": 1}
        # triplet 8th grid = 8 ticks; at tick 1, wait 7 ticks @ 120 BPM
        expected = 10.0 + 7 * (0.5 / 24)
        self.assertAlmostEqual(next_grid_monotonic(10.0, snap, 8), expected, places=6)

    def test_plan_fire_at_applies_offset(self) -> None:
        snap = {"synced": False, "running": False}
        self.assertAlmostEqual(plan_fire_at(1.0, snap, quantize=False, grid_ticks=6, offset_ms=-21.0), 0.979)

    def test_plan_fire_at_quantize_when_synced(self) -> None:
        snap = {
            "synced": True,
            "running": True,
            "bpm_raw": 120.0,
            "transport_ticks": 1,
            "ticks_in_beat": 1,
        }
        fire = plan_fire_at(10.0, snap, quantize=True, grid_ticks=6, offset_ms=-21.0)
        grid = next_grid_monotonic(10.0, snap, 6)
        self.assertAlmostEqual(fire, grid - 0.021, places=6)


if __name__ == "__main__":
    unittest.main()
