"""SlBenchStateListener registers all loops incl. 0."""

import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

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

    def test_register_tail_peak_meters_scratch_when_seam_weld(self) -> None:
        from scripts.sooperlooper.sl_seam_weld import SCRATCH_LOOP

        by_loop = {0: LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)}
        session = MagicMock()
        listener = SlBenchStateListener(by_loop, session=session)
        listener.register(MagicMock(), num_loops=1)
        listener.register_tail_peak(0)
        self.assertEqual(listener._tail_peak_owner, 0)
        self.assertEqual(listener._tail_peak_loop, SCRATCH_LOOP)
        session.register_tail_peak.assert_called_once_with(
            SCRATCH_LOOP, update_ms=ANY
        )

    def test_scratch_peak_survives_having_no_footswitch(self) -> None:
        """Regression: the _by_loop lookup used to run first and swallow these.

        The scratch loop has no pad bound while seam weld is on, so every tail
        peak was dropped, _tail_saw_loud never set, and the tail got cut at the
        fixed TAIL_MAX_S window instead of at the note's actual decay.
        """
        from scripts.sooperlooper.sl_seam_weld import SCRATCH_LOOP

        fs0 = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        listener = SlBenchStateListener({0: fs0}, session=_session())
        self.assertIsNone(listener._by_loop.get(SCRATCH_LOOP))
        listener._tail_peak_loop = SCRATCH_LOOP
        listener._tail_peak_owner = 0
        fs0._tail_capture = True
        listener.on_update("/sl/bench/state", SCRATCH_LOOP, "in_peak_meter", 0.9)
        self.assertTrue(fs0._in_peak_seen)
        self.assertTrue(fs0._tail_saw_loud, "release peak must reach the owner loop")

    def test_in_peak_from_scratch_routes_to_owner_loop(self) -> None:
        from scripts.sooperlooper.sl_seam_weld import SCRATCH_LOOP

        fs0 = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs1 = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0)
        listener = SlBenchStateListener({0: fs0, 1: fs1}, session=_session())
        listener._tail_peak_loop = SCRATCH_LOOP
        listener._tail_peak_owner = 0
        listener.on_update("/sl/bench/state", SCRATCH_LOOP, "in_peak_meter", 0.5)
        self.assertEqual(fs0._in_peak, 0.5)
        self.assertEqual(fs1._in_peak, 0.0)

    def test_unregister_tail_peak_clears_loop(self) -> None:
        session = MagicMock()
        listener = SlBenchStateListener({}, session=session)
        listener._tail_peak_loop = 2
        listener.unregister_tail_peak()
        self.assertIsNone(listener._tail_peak_loop)
        session.unregister_tail_peak.assert_called_once_with(2)

    def test_wire_tail_capture_sets_hooks(self) -> None:
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        listener = SlBenchStateListener({0: fs}, session=_session())
        listener.wire_tail_capture([fs])
        # Bound methods compare equal but are freshly created per attribute
        # access, so `is` never holds — assertEqual is the correct identity here.
        self.assertEqual(fs._on_tail_capture_begin, listener.register_tail_peak)
        self.assertEqual(fs._on_tail_capture_end, listener.unregister_tail_peak)


if __name__ == "__main__":
    unittest.main()
