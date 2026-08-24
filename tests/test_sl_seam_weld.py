"""SeamWeldWorker OSC orchestration."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
