"""Looper engine restart — explicit session events, not config sentinels."""

from __future__ import annotations

import tempfile
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


class SentinelRemovedTests(unittest.TestCase):
    def test_no_global_config_probe_in_listener(self) -> None:
        text = Path("scripts/sooperlooper/sl_bench_listener.py").read_text(encoding="utf-8")
        self.assertNotIn("GLOBAL_CONFIG_PROBE", text)
        self.assertNotIn("ENGINE_CONFIG_PROBE", text)
        client = MagicMock()
        listener = SlBenchStateListener({})
        listener.register(client, num_loops=1)
        global_regs = [
            c for c in client.send_message.call_args_list if c.args[0] == "/register_auto_update"
        ]
        self.assertEqual(global_regs, [])

    def test_grid_sync_has_no_restart_sentinel(self) -> None:
        text = Path("scripts/sooperlooper/sl_grid_sync.py").read_text(encoding="utf-8")
        self.assertNotIn("RESTART_SENTINEL", text)
        self.assertNotIn("ENGINE_CONFIG_PROBE", text)


if __name__ == "__main__":
    unittest.main()
