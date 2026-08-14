"""SlBenchStateListener registers all loops incl. 0."""

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.apc_footswitch import LoopFootswitch
from scripts.sooperlooper.sl_bench_listener import SlBenchStateListener


class SlBenchStateListenerTests(unittest.TestCase):
    def test_register_all_loops(self) -> None:
        by_loop = {
            0: LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0),
            1: LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0),
        }
        listener = SlBenchStateListener(by_loop)
        client = MagicMock()
        listener.register(client, num_loops=2)
        paths = [c.args[0] for c in client.send_message.call_args_list]
        self.assertIn("/sl/0/register_auto_update", paths)
        self.assertIn("/sl/1/register_auto_update", paths)

    def test_on_update_routes_to_footswitch(self) -> None:
        fs = LoopFootswitch(loop=3, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 40)
        listener = SlBenchStateListener({3: fs})
        listener.on_update("/sl/bench/state", 3, "state", 4.0)
        self.assertEqual(fs.state, "playing")


if __name__ == "__main__":
    unittest.main()
