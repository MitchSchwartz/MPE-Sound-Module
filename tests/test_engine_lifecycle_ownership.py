"""Every path that starts the engine must say so.

The bench re-applies grid config when it sees ``looper.engine.started``
(`sooperlooper-apc-bench.py:333`). A path that starts a fresh engine without
emitting it leaves a split brain: the engine has never heard of the grid, and
the bench believes it already applied one. Nothing on the surface distinguishes
that from a working session until a clip records to the wrong length.

`mpe-sooperlooper.service` emits via `ExecStartPost`. `restart-sooperlooper.sh`
— the documented remedy for an orphan, and the one you reach for mid-session —
launched the binary directly with `setsid nohup`, so that hook never fired and
`mpe looper sl-restart` was the split brain.

Positive controls included, so this cannot pass by finding no starters.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVENT = "looper.engine.started"

#: Launching the engine binary directly, as opposed to talking to systemd.
STARTS_ENGINE = re.compile(r"\$\{SOOP_BIN\}|\bsooperlooper\s+-q\b")

#: An actual emit CALL, not the event named in a comment. The first version of
#: this file matched the bare string anywhere in the script, and a comment
#: mentioning the event satisfied it — so deleting the emit while leaving the
#: comment that explains it passed. That is the "prose cannot fail a build"
#: failure this branch exists to end, reproduced inside its own guard.
EMITS = re.compile(r"^\s*mpe_session_event_emit\s+looper\.engine\.started",
                   re.MULTILINE)


def shell_scripts() -> list[Path]:
    return sorted(REPO.glob("scripts/**/*.sh"))


def starters() -> list[Path]:
    return [p for p in shell_scripts() if STARTS_ENGINE.search(p.read_text())]


class EngineStartAnnouncesItselfTests(unittest.TestCase):

    def test_the_scan_finds_the_starters_we_know_about(self) -> None:
        """Positive control: an empty list would make the check below vacuous."""
        names = {p.name for p in starters()}
        self.assertIn("restart-sooperlooper.sh", names)
        self.assertTrue(len(names) >= 1)

    def test_every_engine_starter_emits_the_event(self) -> None:
        silent = [
            p.relative_to(REPO).as_posix()
            for p in starters()
            if not EMITS.search(p.read_text())
        ]
        self.assertEqual(
            silent, [],
            f"these start a fresh engine without emitting {EVENT}, so the bench "
            "keeps applying the previous engine's grid to a process that has "
            "never heard of it",
        )

    def test_the_systemd_path_still_emits_via_its_hook(self) -> None:
        """The service does it with ExecStartPost rather than inline."""
        unit = (REPO / "config" / "mpe-sooperlooper.service").read_text()
        hook = re.search(r"^ExecStartPost=.*?(\S+\.sh)\s*$", unit, re.MULTILINE)
        self.assertIsNotNone(hook, "no ExecStartPost on the sooperlooper unit")
        target = REPO / "scripts" / "sooperlooper" / Path(hook.group(1)).name
        self.assertTrue(target.is_file(), f"{target} is missing")
        self.assertTrue(EMITS.search(target.read_text()))


if __name__ == "__main__":
    unittest.main()
