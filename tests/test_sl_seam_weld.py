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
        self.assertIn((f"/sl/{SCRATCH_LOOP}/set", ["dry", 0.0]), sent)
        self.assertIn((f"/sl/{SCRATCH_LOOP}/set", ["feedback", 0.0]), sent)

    def test_start_scratch_record_reapplies_silence(self) -> None:
        sent: list[tuple[str, list]] = []

        def capture(path: str, args: list) -> None:
            sent.append((path, args))

        worker = SeamWeldWorker(capture, log=lambda *_a, **_k: None)
        worker.start_scratch_record(SCRATCH_LOOP)

        self.assertIn((f"/sl/{SCRATCH_LOOP}/set", ["wet", 0.0]), sent)
        self.assertIn((f"/sl/{SCRATCH_LOOP}/hit", ["record"]), sent)


if __name__ == "__main__":
    unittest.main()
