"""The env file must never keep a setting the graph never proved.

Regression for 2026-09-01: `set-surge-audio.sh --buffer 64` was killed
mid-flight by the touch UI's 45 s `subprocess.run` timeout. The script writes
the new value to /etc/mpe/mpe.env *before* validating it, and its rollback was
an ordinary code path — so the kill skipped the rollback and left an untested
buffer in the file. The appliance then booted into it, dead.

These are structural checks, not a functional run: the script hardcodes
ENV_FILE=/etc/mpe/mpe.env and drives systemd, so exercising it for real needs
root and a live graph. They still fail if the trap is removed or if a new write
site forgets to arm it, which is the regression that actually happened.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "set-surge-audio.sh"
UI = REPO / "patch_browser" / "surge_audio.py"


class RollbackSurvivesDeathTests(unittest.TestCase):
    def setUp(self):
        self.src = SCRIPT.read_text(encoding="utf-8")

    def test_restore_is_trapped_on_every_death_signal(self):
        m = re.search(r"^trap\s+(\S+)\s+(.+)$", self.src, re.M)
        self.assertIsNotNone(m, "no trap installed — a kill loses the rollback")
        signals = set(m.group(2).split())
        for sig in ("EXIT", "INT", "TERM", "HUP"):
            self.assertIn(sig, signals, f"trap does not cover {sig}")

    def test_every_env_write_arms_the_trap(self):
        """A write site that forgets `_env_dirty=true` is invisible to the trap."""
        writes = list(
            re.finditer(r"^\s*_update_env_var\s+(MPE_\w+)\s+\"([^\"]+)\"", self.src, re.M)
        )
        self.assertTrue(writes, "no env writes found — test is looking at the wrong file")
        checked = 0
        for m in writes:
            value = m.group(2)
            # A write whose VALUE is a saved previous reading is a restore, not
            # a change — those are the rollback itself and must not arm it.
            if "_prev_" in value or "_old_" in value:
                continue
            checked += 1
            line_no = self.src[:m.start()].count("\n") + 1
            window = self.src[max(0, m.start() - 300):m.start()]
            self.assertIn(
                "_env_dirty=true", window,
                f"env write at line {line_no} does not arm the rollback trap",
            )
        self.assertGreaterEqual(
            checked, 3, "expected buffer/periods/sample-rate writes to be checked"
        )

    def test_commit_happens_only_after_the_graph_is_proven(self):
        commit = self.src.index("_env_committed=true\n\necho -n \"Applied\"")
        promote = self.src.index('mpe_promote_surge_planned "settings-change"')
        self.assertGreater(
            commit, promote,
            "settings are committed before the graph proves them",
        )

    def test_trap_checks_the_commit_flag(self):
        body = self.src[self.src.index("_restore_env_on_death() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("_env_committed", body,
                      "trap would clobber a successfully applied setting")

    def test_ui_timeout_leaves_room_for_a_graph_restart(self):
        """The kill that caused this. Keep it well clear of a slow restart."""
        m = re.search(r"AUDIO_SWITCH_TIMEOUT_S\s*=\s*([\d.]+)", UI.read_text("utf-8"))
        self.assertIsNotNone(m)
        self.assertGreaterEqual(
            float(m.group(1)), 90.0,
            "45 s killed the script mid-write on 2026-09-01",
        )


if __name__ == "__main__":
    unittest.main()
