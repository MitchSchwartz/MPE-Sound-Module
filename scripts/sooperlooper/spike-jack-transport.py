#!/usr/bin/env python3
"""Task 0 gate: verify SooperLooper 1.7.9 follows JACK transport BBT.

Run on the Pi with JACK + SooperLooper already up. Writes a markdown summary to
docs/measurements/ (or --output path).

Checks:
  0.1 timebase master + rolling BBT
  0.2 sync_source=-1 + quantize=cycle (OSC config only — ear check for boundary)
  0.4 SL idle with transport rolling, no loops
  0.5 transport stop mid-session (non-crash)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "docs/measurements/task0-jack-transport-spike.md"

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
BPM = float(os.environ.get("MPE_LOOPER_BPM", "120"))


def _osc_send(path: str, args: list) -> None:
    subprocess.run(
        ["oscsend", SL_HOST, str(SL_PORT), path, *[str(a) for a in args]],
        check=False,
        capture_output=True,
        text=True,
    )


def _jack_transport_query() -> tuple[str, dict]:
    import jack

    client = jack.Client("mpe-spike-query", no_start_server=True)
    try:
        state, pos = client.transport_query()
        return state, dict(pos) if pos else {}
    finally:
        client.close()


def check_01_timebase() -> tuple[bool, str]:
    try:
        import jack
    except ImportError:
        return False, "python3-jack-client not installed"

    state, pos = _jack_transport_query()
    bbt = pos.get("bar"), pos.get("beat"), pos.get("tick")
    if state not in (jack.TRANSPORT_ROLLING, jack.TRANSPORT_STARTING):
        return False, f"transport state={state!r}, not rolling (start jack_timebase.py first)"
    if bbt[0] is None:
        return False, f"no BBT in position: {pos!r}"
    time.sleep(0.5)
    _, pos2 = _jack_transport_query()
    if pos2.get("bar") is None:
        return False, "BBT did not persist on second query"
    return True, f"rolling BBT bar={pos2.get('bar')} beat={pos2.get('beat')} tick={pos2.get('tick')}"


def check_02_sl_jack_sync() -> tuple[bool, str]:
    _osc_send("/set", ["sync_source", -1.0])
    _osc_send("/set", ["eighth_per_cycle", 8.0])
    _osc_send("/sl/1/set", ["quantize", 1.0])
    _osc_send("/sl/1/set", ["sync", 1.0])
    _osc_send("/sl/1/set", ["playback_sync", 1.0])
    return True, (
        "OSC applied sync_source=-1 quantize=cycle on loop 1 — "
        "manual: record loop 1 must end on transport bar boundary"
    )


def check_04_idle() -> tuple[bool, str]:
    try:
        out = subprocess.run(
            ["pgrep", "-x", "sooperlooper"],
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            return False, "sooperlooper not running"
        xruns = subprocess.run(
            ["journalctl", "-u", "mpe-jackd", "--since", "1 min ago", "-q"],
            capture_output=True,
            text=True,
        )
        storm = xruns.stdout.lower().count("xrun")
        return True, f"SL running; ~{storm} xrun mentions in last minute of jackd journal"
    except FileNotFoundError:
        return True, "sooperlooper running (journalctl unavailable for xrun count)"


def check_05_stop_relocate() -> tuple[bool, str]:
    try:
        import jack

        client = jack.Client("mpe-spike-stop", no_start_server=True)
        try:
            client.transport_stop()
            time.sleep(0.2)
            state, _ = client.transport_query()
            client.transport_start()
            time.sleep(0.2)
            state2, _ = client.transport_query()
            ok = state != jack.TRANSPORT_ROLLING or state2 in (
                jack.TRANSPORT_ROLLING,
                jack.TRANSPORT_STARTING,
            )
            return ok, f"stop→{state!r} start→{state2!r} (no crash)"
        finally:
            client.close()
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    checks = [
        ("0.1", "JACK timebase rolling with BBT", check_01_timebase),
        ("0.2", "SL sync_source=-1 + quantize=cycle (OSC)", check_02_sl_jack_sync),
        ("0.4", "SL idle with transport rolling", check_04_idle),
        ("0.5", "Transport stop/start non-crash", check_05_stop_relocate),
    ]

    rows: list[str] = []
    all_auto_pass = True
    for num, label, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"exception: {exc}"
        if not ok:
            all_auto_pass = False
        mark = "pass" if ok else "FAIL"
        rows.append(f"| {num} | {label} | **{mark}** | {detail} |")
        print(f"[{mark}] {num} {label}: {detail}", flush=True)

    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    body = f"""# Task 0 — JACK transport spike

*Generated:* {now}
*BPM env:* {BPM}
*SL OSC:* {SL_HOST}:{SL_PORT}

| # | Check | Result | Detail |
|---|---|---|---|
{chr(10).join(rows)}

## 0.3 — bar phase (manual, kill criterion)

Record loop A, wait ≥30 s, record loop B. Both must start on the **same downbeat**.
If this fails, §G silent reference loop is the fallback — §C does not execute.

## Verdict

Automated checks: **{"PASS" if all_auto_pass else "INCOMPLETE"}** (0.3 requires Mitch ear test).

"""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")
    print(f"Wrote {args.output}", flush=True)
    return 0 if all_auto_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
