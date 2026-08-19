"""Looper engine restart — explicit session events, not config sentinels."""

from __future__ import annotations

import tempfile
import unittest.mock
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from patch_browser.session_events import emit_event
from scripts.sooperlooper.looper_engine_events import (
    LOOPER_ENGINE_STARTED,
    LooperEngineEventWatch,
)
from scripts.sooperlooper.sl_bench_listener import SlBenchStateListener


class LooperEngineEventWatchTests(unittest.TestCase):
    def test_bootstrap_does_not_fire_on_existing_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            emit_event(LOOPER_ENGINE_STARTED, source="test", ts=100.0, run=run)
            seen: list[int] = []
            watch = LooperEngineEventWatch(lambda: seen.append(1), run=run)
            watch.poll()
            watch.poll()
            self.assertEqual(seen, [])

    def test_new_event_after_bootstrap_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            emit_event(LOOPER_ENGINE_STARTED, source="test", ts=100.0, run=run)
            seen: list[int] = []
            watch = LooperEngineEventWatch(lambda: seen.append(1), run=run)
            watch.poll()
            emit_event(LOOPER_ENGINE_STARTED, source="test", ts=200.0, run=run)
            watch.poll()
            self.assertEqual(seen, [1])



    def test_poll_skips_unchanged_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            emit_event(LOOPER_ENGINE_STARTED, source="test", ts=100.0, run=run)
            seen: list[int] = []
            watch = LooperEngineEventWatch(lambda: seen.append(1), run=run)
            watch.poll()
            with unittest.mock.patch(
                "scripts.sooperlooper.looper_engine_events._latest_looper_started_ts"
            ) as mock_tail:
                watch.poll()
                mock_tail.assert_not_called()
            self.assertEqual(seen, [])

    def test_tail_read_finds_latest_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            path = run / "events.jsonl"
            emit_event(LOOPER_ENGINE_STARTED, source="test", ts=10.0, run=run)
            emit_event("engine.started", source="test", ts=11.0, run=run)
            emit_event(LOOPER_ENGINE_STARTED, source="test", ts=99.0, run=run)
            from scripts.sooperlooper.looper_engine_events import _latest_looper_started_ts

            self.assertEqual(_latest_looper_started_ts(path), 99.0)


    def test_full_read_backstop_when_event_outside_tail_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            path = run / "events.jsonl"
            emit_event(LOOPER_ENGINE_STARTED, source="test", ts=42.0, run=run)
            filler = '{"ts":1.0,"event":"mode.changed","source":"test"}\n'
            with path.open("ab") as handle:
                while path.stat().st_size <= 65536:
                    handle.write(filler.encode("utf-8"))
            from scripts.sooperlooper.looper_engine_events import _latest_looper_started_ts

            self.assertGreater(path.stat().st_size, 65536)
            self.assertEqual(_latest_looper_started_ts(path), 42.0)

class SentinelRemovedTests(unittest.TestCase):
    def test_no_global_config_probe_in_listener(self) -> None:
        text = Path("scripts/sooperlooper/sl_bench_listener.py").read_text(encoding="utf-8")
        self.assertNotIn("GLOBAL_CONFIG_PROBE", text)
        self.assertNotIn("ENGINE_CONFIG_PROBE", text)
        session = MagicMock()
        listener = SlBenchStateListener({}, session=session)
        listener.register(MagicMock(), num_loops=1)
        session.register_bench.assert_called_once_with(num_loops=1)

    def test_grid_sync_has_no_restart_sentinel(self) -> None:
        text = Path("scripts/sooperlooper/sl_grid_sync.py").read_text(encoding="utf-8")
        self.assertNotIn("RESTART_SENTINEL", text)
        self.assertNotIn("ENGINE_CONFIG_PROBE", text)


if __name__ == "__main__":
    unittest.main()
