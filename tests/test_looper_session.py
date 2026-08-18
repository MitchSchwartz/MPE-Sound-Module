"""Phase 3M — merged looper session unit and entry point."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class LooperSessionPhase3MTests(unittest.TestCase):
    def test_merged_unit_replaces_bench_and_hud_units(self) -> None:
        self.assertTrue((REPO / "config/mpe-looper-session.service").is_file())
        self.assertFalse((REPO / "config/mpe-apc-bench.service").exists())
        self.assertFalse((REPO / "config/sl-hud-monitor.service").exists())

    def test_install_units_enables_looper_session(self) -> None:
        install = (REPO / "scripts/install-units.sh").read_text(encoding="utf-8")
        block = install.split("ENABLED=(", 1)[1].split(")", 1)[0]
        self.assertIn("mpe-looper-session", block)
        self.assertNotIn("mpe-apc-bench", block)
        self.assertNotIn("sl-hud-monitor", block)

    def test_sync_source_restart_sentinel_removed(self) -> None:
        grid_sync = (REPO / "scripts/sooperlooper/sl_grid_sync.py").read_text(
            encoding="utf-8"
        )
        listener = (REPO / "scripts/sooperlooper/sl_bench_listener.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("RESTART_SENTINEL", grid_sync)
        self.assertNotIn("GLOBAL_SENTINEL", listener)
        self.assertIn("ENGINE_CONFIG_PROBE", grid_sync)
        self.assertIn("smart_eighths", grid_sync)

    def test_looper_session_entry_exists(self) -> None:
        entry = REPO / "scripts/looper-session.py"
        self.assertTrue(entry.is_file())
        text = entry.read_text(encoding="utf-8")
        self.assertIn("run_session", text)

    def test_hud_runs_on_background_thread(self) -> None:
        mod = (REPO / "scripts/sooperlooper/looper_session.py").read_text(encoding="utf-8")
        self.assertIn("start_hud_thread", mod)
        self.assertIn("threading.Thread", mod)
        self.assertIn("run_bench", mod)


if __name__ == "__main__":
    unittest.main()
