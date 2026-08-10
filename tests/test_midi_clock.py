"""Tests for MIDI clock timing and port discovery."""

import tempfile
import time
import unittest
from pathlib import Path

from patch_browser.midi_clock import (
    MidiClockTracker,
    bpm_from_tick_interval,
    find_clock_input_port_index,
    find_clock_output_port_index,
    looper_hud_label,
    read_clock_state,
    should_skip_clock_port,
    stabilize_display_bpm,
    tick_interval_seconds,
    write_clock_state,
)


class TestTickInterval(unittest.TestCase):
    def test_120_bpm(self) -> None:
        self.assertAlmostEqual(tick_interval_seconds(120.0), 60.0 / 120.0 / 24.0)

    def test_bpm_from_interval_round_trip(self) -> None:
        interval = tick_interval_seconds(92.0)
        self.assertAlmostEqual(bpm_from_tick_interval(interval), 92.0, places=3)

    def test_rejects_non_positive_bpm(self) -> None:
        with self.assertRaises(ValueError):
            tick_interval_seconds(0)


class TestPortDiscovery(unittest.TestCase):
    def test_skips_internal_ports(self) -> None:
        ports = [
            "Surge XT CLI:Surge XT CLI MIDI 1 14:0",
            "Midi Through:Midi Through Port-0 14:0",
            "UM-ONE:UM-ONE MIDI 1 20:0",
        ]
        self.assertEqual(find_clock_output_port_index(ports), 2)

    def test_prefer_looper_input(self) -> None:
        ports = [
            "LUMI Keys:LUMI Keys MIDI 1 20:0",
            "Boss RC-5:Boss RC-5 MIDI 1 21:0",
        ]
        self.assertEqual(find_clock_input_port_index(ports), 1)

    def test_prefer_substring(self) -> None:
        ports = [
            "interface A: MIDI 1 20:0",
            "Boss RC-5:Boss RC-5 MIDI 1 21:0",
        ]
        self.assertEqual(
            find_clock_output_port_index(ports, prefer_substring="RC-5"),
            1,
        )

    def test_should_skip_surge(self) -> None:
        self.assertTrue(should_skip_clock_port("Surge XT CLI MIDI 1"))


class TestMidiClockTracker(unittest.TestCase):
    def test_tracks_bpm_from_clock(self) -> None:
        tracker = MidiClockTracker(stale_after_s=10.0, ema_alpha=1.0)
        t0 = 1000.0
        interval = tick_interval_seconds(120.0)
        tracker.on_message([0xFA], t0)
        tick = 0
        t = t0
        while tick < 72:
            t += interval
            tracker.on_message([0xF8], t)
            tick += 1
        snap = tracker.snapshot(now=t)
        self.assertTrue(snap["synced"])
        self.assertTrue(snap["running"])
        self.assertIsNotNone(snap["bpm"])
        self.assertAlmostEqual(float(snap["bpm"]), 120.0, delta=1.0)

    def test_display_hysteresis(self) -> None:
        self.assertEqual(stabilize_display_bpm(110.4, 110), 110)
        self.assertEqual(stabilize_display_bpm(110.59, 110), 110)
        self.assertEqual(stabilize_display_bpm(111.0, 110), 111)
        self.assertEqual(stabilize_display_bpm(108.3, 110), 108)

    def test_stop_clears_running(self) -> None:
        tracker = MidiClockTracker()
        tracker.on_message([0xFA], 1.0)
        tracker.on_message([0xFC], 1.1)
        snap = tracker.snapshot(now=1.2)
        self.assertFalse(snap["running"])


class TestClockStateFile(unittest.TestCase):
    def test_read_write_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clock.json"
            write_clock_state(
                {
                    "connected": True,
                    "synced": True,
                    "running": True,
                    "bpm": 110,
                    "port": "Boss RC-5",
                    "updated_at": time.monotonic(),
                },
                path=path,
            )
            snap = read_clock_state(path=path, stale_after_s=30.0)
            self.assertTrue(snap["daemon_online"])
            self.assertTrue(snap["synced"])
            self.assertEqual(snap["bpm"], 110)

    def test_looper_hud_label(self) -> None:
        self.assertEqual(looper_hud_label({"daemon_online": True, "bpm": 120}), "120")
        self.assertEqual(looper_hud_label({"daemon_online": False}), "LOOP")


if __name__ == "__main__":
    unittest.main()
