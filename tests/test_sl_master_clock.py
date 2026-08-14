"""Saved master clock reference for internal grid sync."""

import unittest

from scripts.sooperlooper.sl_master_clock import (
    apply_internal_master,
    tempo_from_cycle_len,
)


class MasterClockTests(unittest.TestCase):
    def test_tempo_from_two_second_bar(self) -> None:
        self.assertAlmostEqual(tempo_from_cycle_len(2.0), 120.0)

    def test_internal_master_uses_sync_source_minus_three(self) -> None:
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        clock = {"tempo": 120.0, "cycle_len": 2.0, "eighth_per_cycle": 8}
        apply_internal_master(send, clock, num_loops=2)
        self.assertIn(("/set", ["sync_source", -3.0]), sent)
        self.assertIn(("/set", ["tempo", 120.0]), sent)
        self.assertIn(("/sl/1/set", ["sync", 1.0]), sent)


if __name__ == "__main__":
    unittest.main()
