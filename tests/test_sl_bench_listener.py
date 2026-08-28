"""SlBenchStateListener registers all loops incl. 0."""

from tests import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import ANY, MagicMock

from scripts.sooperlooper.apc_footswitch import LoopFootswitch
from scripts.sooperlooper.sl_bench_listener import SlBenchStateListener


def _session():
    from unittest.mock import MagicMock
    return MagicMock()


class SlBenchStateListenerTests(unittest.TestCase):
    def test_register_all_loops(self) -> None:
        by_loop = {
            0: LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0),
            1: LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0),
        }
        session = MagicMock()
        listener = SlBenchStateListener(by_loop, session=session)
        listener.register(MagicMock(), num_loops=2)
        session.register_bench.assert_called_once_with(num_loops=2)

    def test_on_update_routes_to_footswitch(self) -> None:
        fs = LoopFootswitch(loop=3, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 40)
        listener = SlBenchStateListener({3: fs}, session=_session())
        listener.on_update("/sl/bench/state", 3, "state", 4.0)
        self.assertEqual(fs.state, "playing")

    def test_on_update_routes_loop_len_to_surface_without_footswitch(self) -> None:
        surface = MagicMock()
        listener = SlBenchStateListener({}, session=_session())
        listener.attach_surface(surface)
        listener.on_update("/sl/bench/state", 7, "loop_len", 4.0)
        surface.on_loop_len.assert_called_once_with(7, 4.0)
