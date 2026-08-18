"""SlBenchStateListener registers all loops incl. 0."""

import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import ANY, MagicMock

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

    def test_register_tail_peak_scoped_to_one_loop(self) -> None:
        by_loop = {0: LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)}
        listener = SlBenchStateListener(by_loop)
        client = MagicMock()
        listener.register(client, num_loops=1)
        client.reset_mock()
        listener.register_tail_peak(0)
        self.assertEqual(listener._tail_peak_loop, 0)
        paths = [c.args[0] for c in client.send_message.call_args_list]
        self.assertEqual(paths, ["/sl/0/register_auto_update"])

    def test_in_peak_ignored_for_other_loops(self) -> None:
        fs0 = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs1 = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0)
        listener = SlBenchStateListener({0: fs0, 1: fs1})
        listener._tail_peak_loop = 0
        listener.on_update("/sl/bench/state", 1, "in_peak_meter", 0.5)
        self.assertEqual(fs0._in_peak, 0.0)
        self.assertEqual(fs1._in_peak, 0.0)
        listener.on_update("/sl/bench/state", 0, "in_peak_meter", 0.5)
        self.assertEqual(fs0._in_peak, 0.5)
        self.assertEqual(fs1._in_peak, 0.0)

    def test_unregister_tail_peak_clears_loop(self) -> None:
        listener = SlBenchStateListener({})
        client = MagicMock()
        listener._osc_client = client
        listener._tail_peak_loop = 2
        listener.unregister_tail_peak()
        self.assertIsNone(listener._tail_peak_loop)
        client.send_message.assert_called_once_with(
            "/sl/2/unregister_auto_update",
            ["in_peak_meter", ANY, "/sl/bench/state"],
        )

    def test_wire_tail_capture_sets_hooks(self) -> None:
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        listener = SlBenchStateListener({0: fs})
        listener.wire_tail_capture([fs])
        self.assertIs(fs._on_tail_capture_begin, listener.register_tail_peak)
        self.assertIs(fs._on_tail_capture_end, listener.unregister_tail_peak)


if __name__ == "__main__":
    unittest.main()
