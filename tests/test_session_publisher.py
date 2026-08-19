"""Snapshot publisher — work order task 5 (unblocked by the task 4 liveness spike)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PUBLISHER = REPO / "scripts" / "session-snapshot-publisher.py"
UNIT = REPO / "config" / "mpe-session-publisher.service"


class SessionPublisherTests(unittest.TestCase):
    def test_publisher_exists_and_is_executable(self) -> None:
        self.assertTrue(PUBLISHER.is_file())
        self.assertTrue(PUBLISHER.stat().st_mode & 0o111)

    def test_publishes_a_snapshot_with_an_allocated_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            for expected in (1, 2):
                proc = subprocess.run(
                    [sys.executable, str(PUBLISHER), "--once", "--run-dir", str(run)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                snap = json.loads((run / "session.snapshot.json").read_text(encoding="utf-8"))
                self.assertEqual(snap["seq"], expected)

    def test_rejects_a_non_positive_interval(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(PUBLISHER), "--interval", "0"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_builds_in_process_never_by_reinvoking_the_module_cli(self) -> None:
        """418 ms per CLI invocation vs 42 ms in-process — 360 ms of it interpreter start."""
        text = PUBLISHER.read_text(encoding="utf-8")
        self.assertIn("from patch_browser.session_snapshot import", text)
        self.assertNotIn("session_snapshot.py", text.split('"""', 2)[-1])

    def test_unit_is_installed_but_not_enabled_by_default(self) -> None:
        """A new always-on poll is an operator decision, not a deploy side effect."""
        self.assertTrue(UNIT.is_file())
        install = (REPO / "scripts" / "install-units.sh").read_text(encoding="utf-8")
        enabled = install.split("ENABLED=(", 1)[1].split(")", 1)[0]
        disabled = install.split("DISABLED=(", 1)[1].split("\n)", 1)[0]
        self.assertNotIn("mpe-session-publisher", enabled)
        self.assertIn("mpe-session-publisher", disabled)

    def test_unit_keeps_the_publisher_off_the_realtime_plane(self) -> None:
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("CPUSchedulingPolicy=other", text)
        self.assertEqual(re.findall(r"^LimitRTPRIO=", text, re.M), [])
        self.assertIn("Nice=", text)

    def test_unit_cites_the_measurement_behind_its_interval(self) -> None:
        """Cadence is a measured decision here; the number must travel with the unit."""
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("systemd-liveness-cost-2026-08-19.md", text)


if __name__ == "__main__":
    unittest.main()
