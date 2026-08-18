"""SooperLooper grid-sync OSC configuration."""

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.sl_grid_sync import (
    apply_freeform,
    apply_grid_sync,
    establish_grid_clock,
)


class SlGridSyncTests(unittest.TestCase):
    def test_grid_default_clock_is_internal_and_self_sufficient(self) -> None:
        """Default grid must not depend on a process we don't start.

        sync_source=-1 (JACK transport) with no timebase master parks SL in
        WaitStart forever. Internal sync always has a pulse.
        """
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        apply_grid_sync(send, num_loops=4, fade_samples=64, bpm=100.0)

        self.assertIn(("/set", ["sync_source", -3.0]), sent)
        self.assertIn(("/set", ["tempo", 100.0]), sent)
        # At startup no grid exists yet, so every loop must be free-form: the
        # take that defines the grid cannot be quantized to a bar that has not
        # been established.
        for loop in range(4):
            self.assertIn((f"/sl/{loop}/set", ["quantize", 0.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["sync", 0.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["round", 0.0]), sent)

    def test_defining_take_is_genuinely_free_form(self) -> None:
        """Before a grid exists the take must not be quantized OR rounded.

        Leaving these on stretched a short first take up to the end of a cycle
        derived from the previous session's tempo.
        """
        from scripts.sooperlooper.sl_grid_sync import set_grid_active

        sent: list[tuple[str, list]] = []
        set_grid_active(lambda p, a: sent.append((p, a)), num_loops=2, active=False)
        for loop in range(2):
            self.assertIn((f"/sl/{loop}/set", ["quantize", 0.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["sync", 0.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["round", 0.0]), sent)

    def test_grid_active_quantizes_but_never_rounds(self) -> None:
        from scripts.sooperlooper.sl_grid_sync import set_grid_active

        sent: list[tuple[str, list]] = []
        set_grid_active(lambda p, a: sent.append((p, a)), num_loops=2, active=True)
        for loop in range(2):
            self.assertIn((f"/sl/{loop}/set", ["quantize", 1.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["sync", 1.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["relative_sync", 0.0]), sent)
            # round on top of a quantized stop adds another whole cycle
            self.assertIn((f"/sl/{loop}/set", ["round", 0.0]), sent)

    def test_establish_grid_clock_disables_smart_eighths_before_tempo(self) -> None:
        """Sub-60 BPM first takes must not double the cycle to two bars."""
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        establish_grid_clock(send, 30.0)
        self.assertEqual(
            sent,
            [
                ("/set", ["smart_eighths", 0.0]),
                ("/set", ["eighth_per_cycle", 8.0]),
                ("/set", ["tempo", 30.0]),
            ],
        )

    def test_phase_anchor_helpers(self) -> None:
        from scripts.sooperlooper.sl_grid_sync import (
            detect_loop_wrap,
            should_defer_phase_anchor,
        )

        self.assertTrue(should_defer_phase_anchor(0.08, 2.0, loop_pos_seen=True))
        self.assertFalse(should_defer_phase_anchor(0.01, 2.0, loop_pos_seen=True))
        self.assertTrue(should_defer_phase_anchor(0.0, 2.0, loop_pos_seen=False))
        self.assertTrue(detect_loop_wrap(1.9, 0.01, 2.0))
        self.assertFalse(detect_loop_wrap(0.5, 0.6, 2.0))

    def test_default_fade_samples_is_256(self) -> None:
        from scripts.sooperlooper import sl_grid_sync

        self.assertEqual(sl_grid_sync.DEFAULT_FADE_SAMPLES, 256)

    def test_grid_sync_jack_transport_all_quantized(self) -> None:
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        apply_grid_sync(send, num_loops=4, fade_samples=64, clock="transport")

        self.assertIn(("/set", ["sync_source", -1.0]), sent)
        self.assertIn(("/set", ["eighth_per_cycle", 8.0]), sent)
        self.assertIn(("/set", ["fade_samples", 64.0]), sent)
        for loop in range(4):
            self.assertIn((f"/sl/{loop}/set", ["quantize", 0.0]), sent)
            # SL's own default; forcing 1 delayed a fresh clip by a whole bar
            self.assertIn((f"/sl/{loop}/set", ["playback_sync", 0.0]), sent)

    def test_freeform_disables_sync(self) -> None:
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        apply_freeform(send, num_loops=2)
        self.assertIn(("/set", ["sync_source", 0.0]), sent)
        self.assertIn(("/sl/0/set", ["playback_sync", 0.0]), sent)


if __name__ == "__main__":
    unittest.main()
