#!/usr/bin/env python3
"""Task 0 alternate gate — internal sync phase via tap_tempo (no new clock process).

Run on Pi with SooperLooper up. Applies sync_source=-3 + tempo + tap_tempo pulse,
then prints the manual ear protocol (same as Task 0.3).

If two clips recorded minutes apart share a downbeat, internal sync + tap_tempo is
enough and JACK transport / a timebase master may be unnecessary.

Unverified assumption from 8d7a426 — this script exists to falsify or confirm it
before writing C or running the Python timebase spike.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "docs/measurements/task0-internal-sync-phase-spike.md"

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
BPM = float(os.environ.get("MPE_LOOPER_BPM", "120"))


def _oscsend(path: str, *args: str) -> None:
    subprocess.run(
        ["oscsend", SL_HOST, str(SL_PORT), path, *args],
        check=False,
        capture_output=True,
        text=True,
    )


def apply_internal_sync_phase_anchor(*, bpm: float = BPM) -> None:
    """sync_source=-3 (internal), set tempo, tap_tempo noop to anchor phase."""
    _oscsend("/set", "sync_source", "-3")
    _oscsend("/set", "tempo", str(bpm))
    _oscsend("/set", "eighth_per_cycle", "8")
    _oscsend("/set", "tap_tempo", "0")
    for loop in range(int(os.environ.get("MPE_SL_LOOPS", "16"))):
        prefix = f"/sl/{loop}/set"
        _oscsend(prefix, "quantize", "1")
        _oscsend(prefix, "sync", "1")
        _oscsend(prefix, "playback_sync", "1")


def main() -> int:
    apply_internal_sync_phase_anchor()
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    body = f"""# Task 0 — internal sync + tap_tempo phase check

*Generated:* {now}
*SL OSC:* {SL_HOST}:{SL_PORT}
*BPM:* {BPM}

## OSC applied

- `sync_source = -3` (internal)
- `tempo = {BPM}`
- `tap_tempo = 0` (noop pulse — phase anchor candidate)
- all loops: `quantize=cycle`, `sync=1`, `playback_sync=1`

## Manual protocol (kill criterion — same as Task 0.3)

1. Record loop A (any pad).
2. Wait **≥ 30 s**.
3. Record loop B.
4. **Pass:** both clips start on the **same downbeat** (ear).
5. **Fail:** phase drifts → internal sync cannot carry phase → proceed with JACK
   transport spike (fixed `jack_timebase.py`) or §G fallback.

## Verdict

**PENDING** — Mitch ear test only. No automated pass/fail.
"""
    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(body, encoding="utf-8")
    print(body, flush=True)
    print(f"Wrote {DEFAULT_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
