"""Read ALSA PCM state from /proc/asound (Pi spike instrumentation).

There is **no xrun counter** in `/proc/asound/card*/pcm*/sub*/status` — the
kernel writes `state`, pointers and timestamps, nothing cumulative. Counting
underruns is `looper_alsa_stderr`'s job, from what aplay/arecord report.

`state: XRUN` here is real but transient: ALSA recovers within a period or two,
so polling catches it only by luck. Treat it as corroboration, never coverage.
"""

from __future__ import annotations

from pathlib import Path


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
