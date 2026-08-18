"""Phase 3M — merged looper session unit and entry point."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
LOOPER_SESSION = REPO / "scripts" / "sooperlooper" / "looper_session.py"
INSTALL_UNITS = REPO / "scripts" / "install-units.sh"


def _disabled_units() -> list[str]:
    text = INSTALL_UNITS.read_text(encoding="utf-8")
    block = text.split("DISABLED=(", 1)[1].split(")", 1)[0]
    names = []
    for raw in block.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


class LooperSessionPhase3MTests(unittest.TestCase):
    def test_merged_unit_replaces_bench_and_hud_units(self) -> None:
        self.assertTrue((REPO / "config/mpe-looper-session.service").is_file())
        self.assertFalse((REPO / "config/mpe-apc-bench.service").exists())
        self.assertFalse((REPO / "config/sl-hud-monitor.service").exists())

    def test_install_units_enables_looper_session(self) -> None:
        install = INSTALL_UNITS.read_text(encoding="utf-8")
        block = install.split("ENABLED=(", 1)[1].split(")", 1)[0]
        self.assertIn("mpe-looper-session", block)
        self.assertNotIn("mpe-apc-bench", block)
        self.assertNotIn("sl-hud-monitor", block)

    def test_retired_units_in_disabled_block(self) -> None:
        disabled = _disabled_units()
        self.assertIn("mpe-apc-bench", disabled)
        self.assertIn("sl-hud-monitor", disabled)

    def test_install_units_stops_retired_looper_clients_on_upgrade(self) -> None:
        text = INSTALL_UNITS.read_text(encoding="utf-8")
        self.assertIn("RETIRED_LOOPER_CLIENTS=(mpe-apc-bench sl-hud-monitor)", text)
        self.assertIn('systemctl stop --now "$u.service"', text)

    def test_config_drift_sentinel_removed(self) -> None:
        grid_sync = (REPO / "scripts/sooperlooper/sl_grid_sync.py").read_text(
            encoding="utf-8"
        )
        listener = (REPO / "scripts/sooperlooper/sl_bench_listener.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("RESTART_SENTINEL", grid_sync)
        self.assertNotIn("ENGINE_CONFIG_PROBE", grid_sync)
        self.assertNotIn("GLOBAL_CONFIG_PROBE", listener)
        self.assertIn("looper.engine.started", (REPO / "patch_browser/session_events.py").read_text())

    def test_looper_session_entry_exists(self) -> None:
        entry = REPO / "scripts/looper-session.py"
        self.assertTrue(entry.is_file())
        text = entry.read_text(encoding="utf-8")
        self.assertIn("run_session", text)

    def test_hud_runs_on_background_thread(self) -> None:
        mod = LOOPER_SESSION.read_text(encoding="utf-8")
        self.assertIn("start_hud_thread", mod)
        self.assertIn("threading.Thread", mod)
        self.assertIn("daemon=False", mod)
        self.assertIn("os._exit(1)", mod)
        self.assertIn("hud_writer.close()", mod)

    def test_run_session_bench_only_skips_hud_thread(self) -> None:
        sooper = REPO / "scripts" / "sooperlooper"
        body = f"""
import sys
sys.path.insert(0, "{sooper}")
sys.path.insert(0, "{REPO}")
import looper_session as ls
ls.start_hud_thread = lambda: (_ for _ in ()).throw(AssertionError("hud started"))
class _FakeBench:
    @staticmethod
    def run_bench(argv=None):
        return 0
ls._load_bench_module = lambda: _FakeBench
print("bench-only-ok")
raise SystemExit(ls.run_session(["--bench-only"]))
"""
        result = subprocess.run(
            [sys.executable, "-c", body],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bench-only-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
