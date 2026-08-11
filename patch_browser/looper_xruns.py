"""Read ALSA xrun indicators from /proc/asound (Pi spike instrumentation)."""

from __future__ import annotations

import re
from pathlib import Path

_XRUN_LINE = re.compile(r"^\s*xruns\s*:\s*(\d+)\s*$", re.MULTILINE)


def list_pcm_status_files() -> list[Path]:
    base = Path("/proc/asound")
    if not base.is_dir():
        return []
    paths: list[Path] = []
    for card_dir in sorted(base.glob("card*")):
        for pcm_dir in sorted(card_dir.glob("pcm*")):
            for sub_dir in sorted(pcm_dir.glob("sub*")):
                status = sub_dir / "status"
                if status.is_file():
                    paths.append(status)
    return paths


def read_xrun_counts() -> dict[str, int]:
    """Map ``/proc/asound/.../status`` path → xrun count (0 if absent)."""
    counts: dict[str, int] = {}
    for path in list_pcm_status_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _XRUN_LINE.search(text)
        counts[str(path)] = int(match.group(1)) if match else 0
    return counts


def read_pcm_states() -> dict[str, str]:
    """Map status path → state line (RUNNING, XRUN, …)."""
    states: dict[str, str] = {}
    for path in list_pcm_status_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.strip().startswith("state:"):
                states[str(path)] = line.split(":", 1)[1].strip()
                break
    return states


def any_pcm_xrun_state(states: dict[str, str] | None = None) -> list[str]:
    data = states if states is not None else read_pcm_states()
    return [path for path, state in data.items() if state == "XRUN"]


def total_xruns(counts: dict[str, int] | None = None) -> int:
    data = counts if counts is not None else read_xrun_counts()
    return sum(data.values())
