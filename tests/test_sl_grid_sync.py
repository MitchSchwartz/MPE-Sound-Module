"""SooperLooper grid-sync OSC configuration."""

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.sl_grid_sync import apply_freeform, apply_grid_sync


class SlGridSyncTests(unittest.TestCase):
    def test_grid_sync_jack_transport_all_quantized(self) -> None:
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        apply_grid_sync(send, num_loops=4, fade_samples=64)

        self.assertIn(("/set", ["sync_source", -1.0]), sent)
        self.assertIn(("/set", ["eighth_per_cycle", 8.0]), sent)
        self.assertIn(("/set", ["fade_samples", 64.0]), sent)
        for loop in range(4):
            self.assertIn((f"/sl/{loop}/set", ["quantize", 1.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["sync", 1.0]), sent)
            self.assertIn((f"/sl/{loop}/set", ["playback_sync", 1.0]), sent)

    def test_freeform_disables_sync(self) -> None:
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        apply_freeform(send, num_loops=2)
        self.assertIn(("/set", ["sync_source", 0.0]), sent)
        self.assertIn(("/sl/0/set", ["quantize", 0.0]), sent)


if __name__ == "__main__":
    unittest.main()
