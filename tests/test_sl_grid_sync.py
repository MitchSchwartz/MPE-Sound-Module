"""SooperLooper grid-sync OSC configuration."""

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.sl_grid_sync import apply_freeform, apply_grid_sync


class SlGridSyncTests(unittest.TestCase):
    def test_grid_sync_master_free_others_quantized(self) -> None:
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        apply_grid_sync(send, num_loops=4, master_loop=0)

        self.assertIn(("/set", ["sync_source", 1.0]), sent)
        self.assertIn(("/set", ["eighth_per_cycle", 8.0]), sent)
        self.assertIn(("/sl/0/set", ["quantize", 0.0]), sent)
        self.assertIn(("/sl/0/set", ["sync", 0.0]), sent)
        self.assertIn(("/sl/1/set", ["quantize", 1.0]), sent)
        self.assertIn(("/sl/1/set", ["sync", 1.0]), sent)
        self.assertIn(("/sl/1/set", ["relative_sync", 0.0]), sent)
        self.assertIn(("/sl/1/set", ["playback_sync", 1.0]), sent)

    def test_freeform_disables_sync(self) -> None:
        sent: list[tuple[str, list]] = []

        def send(path: str, args: list) -> None:
            sent.append((path, args))

        apply_freeform(send, num_loops=2)
        self.assertIn(("/set", ["sync_source", 0.0]), sent)
        self.assertIn(("/sl/0/set", ["quantize", 0.0]), sent)


if __name__ == "__main__":
    unittest.main()
