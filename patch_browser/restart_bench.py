"""Restart bench (#112) — trigger and read the whole-stack restart sequence.

The sequence runs as its own systemd unit, not in this process: step 8 restarts
``touch-patch-browser``, so a sequence hosted here would kill itself partway
through. This module therefore only *fires* it and *reads the outcome* — it
never waits for completion, because it does not survive to see it.

The outcome is reported on the next browser startup instead. See
``scripts/restart-bench.sh``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

RESULT_FILE = Path("/run/mpe/restart-bench.result")
LOCK_DIR = Path("/run/mpe/restart-bench.lock")
UNIT = "mpe-restart-bench.service"

RESULT_MAX_BYTES = 8192

# How recently the sequence must have finished for its outcome to be worth
# showing on startup. Longer than the sequence takes (~11 s measured), short
# enough that an ordinary reboot hours later does not resurrect a stale toast.
FRESH_WINDOW_S = 60.0


@dataclass
class RestartBenchResult:
    """Parsed ``restart-bench.result``."""

    started: float = 0.0
    finished: float = 0.0
    result: str = ""
    patch_reload: str = ""
    units: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """False for a half-written file — the sequence crashed or is running.

        The file is written incrementally on purpose, so a partial read is
        expected rather than exceptional. Absence of ``finished`` is the only
        reliable signal that the sequence did not get to the end.
        """
        return self.finished > 0 and bool(self.result)

    @property
    def failed_units(self) -> list[str]:
        return sorted(name for name, status in self.units.items() if status != "ok")

    def is_fresh(self, now: float) -> bool:
        return self.complete and 0 <= (now - self.finished) <= FRESH_WINDOW_S

    def summary(self) -> str:
        """One line for a toast. Names what broke — never just 'partial'."""
        if not self.complete:
            return "Restart did not finish — check the journal"
        if self.result == "ok":
            return "Everything restarted"
        failed = self.failed_units
        if not failed:
            return "Restart finished with problems"
        if len(failed) == 1:
            return f"Restarted — {failed[0]} did not come back"
        return f"Restarted — {len(failed)} services did not come back"


def _parse_float(raw: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def read_result(path: Path | None = None) -> RestartBenchResult | None:
    """Parse the result file; None when absent or unreadable.

    Tolerates missing, empty, malformed and half-written files — all of which
    are reachable states, since the sequence writes as it goes and may be
    interrupted by the very failure it is trying to repair.
    """
    target = path or RESULT_FILE
    try:
        raw = target.open(encoding="utf-8", errors="replace").read(RESULT_MAX_BYTES)
    except OSError:
        return None
    if not raw.strip():
        return None

    out = RestartBenchResult()
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key.startswith("unit."):
            unit = key[len("unit.") :]
            if unit:
                out.units[unit] = value
        elif key == "started":
            out.started = _parse_float(value)
        elif key == "finished":
            out.finished = _parse_float(value)
        elif key == "result":
            out.result = value
        elif key == "patch_reload":
            out.patch_reload = value
    return out


def is_running(lock_dir: Path | None = None) -> bool:
    """True while the sequence holds its lock."""
    return (lock_dir or LOCK_DIR).is_dir()


def trigger() -> tuple[bool, str]:
    """Fire the sequence and return immediately.

    ``--no-block`` is required, not an optimisation: this process is about to be
    restarted by the sequence it just started, so waiting would mean waiting to
    be killed.
    """
    if is_running():
        return False, "Restart already in progress"
    try:
        proc = subprocess.run(
            ["sudo", "systemctl", "start", "--no-block", UNIT],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not start restart: {exc}"[:80]
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return False, f"Could not start restart: {detail[0] if detail else 'unknown'}"[:80]
    return True, "Restarting everything…"
