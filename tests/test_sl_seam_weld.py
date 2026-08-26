"""SeamWeldWorker OSC orchestration."""

import unittest
from pathlib import Path

from scripts.sooperlooper import sl_seam_weld
from scripts.sooperlooper.sl_seam_weld import SCRATCH_LOOP, SeamWeldWorker


class SeamWeldWorkerTests(unittest.TestCase):
    def test_prepare_scratch_clears_and_silences_live_paths(self) -> None:
        sent: list[tuple[str, list]] = []

        def capture(path: str, args: list) -> None:
            sent.append((path, args))

        worker = SeamWeldWorker(capture, log=lambda *_a, **_k: None)
        worker.prepare_scratch(SCRATCH_LOOP)

        self.assertIn((f"/sl/{SCRATCH_LOOP}/hit", ["undo_all"]), sent)
        self.assertIn((f"/sl/{SCRATCH_LOOP}/set", ["wet", 0.0]), sent)
        self.assertIn((f"/sl/{SCRATCH_LOOP}/set", ["feedback", 0.0]), sent)
        self.assertNotIn(
            (f"/sl/{SCRATCH_LOOP}/set", ["dry", 0.0]),
            sent,
            "dry must stay up — it carries input into the record buffer",
        )

    def test_start_scratch_record_arms_then_records(self) -> None:
        sent: list[tuple[str, list]] = []

        def capture(path: str, args: list) -> None:
            sent.append((path, args))

        worker = SeamWeldWorker(capture, log=lambda *_a, **_k: None)
        worker.start_scratch_record(SCRATCH_LOOP)

        self.assertIn((f"/sl/{SCRATCH_LOOP}/set", ["wet", 0.0]), sent)
        hits = [a for p, a in sent if p == f"/sl/{SCRATCH_LOOP}/hit"]
        self.assertEqual(hits, [["pause_off"], ["trigger"], ["record"]])

    def test_default_scratch_loop_is_fourteen(self) -> None:
        self.assertEqual(SCRATCH_LOOP, 14)


class SeamSwapTests(unittest.TestCase):
    """The merged buffer is swapped in at a wrap, never mid-pass."""

    def setUp(self) -> None:
        self.sent: list[tuple[str, list]] = []
        self.worker = SeamWeldWorker(
            lambda path, args: self.sent.append((path, args)),
            log=lambda *_a, **_k: None,
        )
        # Real sleeps would make these tests take seconds of wall clock.
        self._real_sleep = sl_seam_weld.time.sleep
        self.slept: list[float] = []
        sl_seam_weld.time.sleep = self.slept.append

    def tearDown(self) -> None:
        sl_seam_weld.time.sleep = self._real_sleep

    def _hits(self, loop: int) -> list[list]:
        return [a for p, a in self.sent if p == f"/sl/{loop}/hit"]

    def test_never_sets_loop_pos(self) -> None:
        """`set loop_pos` then `trigger` was self-cancelling and never worked.

        trigger restarts from sample 0, so it discards any position set just
        before it — and loop_pos is an SL *output* control besides. Aiming the
        trigger at the wrap is the only thing that actually places the restart.
        """
        self.worker._swap_at_wrap(3, Path("/tmp/merged.wav"), lambda: (0.9, 1.0))
        sets = [(p, a) for p, a in self.sent if a and a[0] == "loop_pos"]
        self.assertEqual(sets, [], "loop_pos is not settable and trigger ignores it")

    def test_swap_order_is_load_then_pause_off_then_trigger(self) -> None:
        self.worker._swap_at_wrap(3, Path("/tmp/merged.wav"), lambda: (0.9, 1.0))
        paths = [p for p, _ in self.sent]
        self.assertEqual(paths[0], "/sl/3/load_loop")
        self.assertEqual(self._hits(3), [["pause_off"], ["trigger"]])

    def test_waits_for_the_wrap_before_triggering(self) -> None:
        """Playhead starts a full loop away — the swap must not fire early."""
        feed = iter([(0.0, 2.0), (0.9, 2.0), (1.7, 2.0), (1.95, 2.0)])
        last = [(1.95, 2.0)]

        def position():
            try:
                last[0] = next(feed)
            except StopIteration:
                pass
            return last[0]

        self.worker._swap_at_wrap(3, Path("/tmp/merged.wav"), position)
        # It polled its way to the wrap rather than swapping on the first read.
        self.assertGreater(len(self.slept), 1)
        self.assertEqual(self._hits(3), [["pause_off"], ["trigger"]])

    def test_swaps_blind_when_there_is_no_position_feed(self) -> None:
        """No feed must still complete the weld — a seam beats a stuck loop."""
        self.worker._swap_at_wrap(3, Path("/tmp/merged.wav"), None)
        self.assertEqual(self._hits(3), [["pause_off"], ["trigger"]])

    def test_zero_length_loop_does_not_spin(self) -> None:
        self.assertIsNone(self.worker._time_to_wrap(lambda: (0.0, 0.0), 0.15))


if __name__ == "__main__":
    unittest.main()
