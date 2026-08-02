#!/usr/bin/env python3
"""Analyze shutdown timing from the previous boot journal + trace files.

Usage:
  ./scripts/shutdown-measure-last.sh
  python3 scripts/shutdown_measure.py [--boot -1]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE = REPO_ROOT / "logs" / "shutdown-trace.jsonl"
DEFAULT_MARKER = REPO_ROOT / "logs" / "shutdown-test-marker.json"
SPLASH_LOG = Path("/tmp/mpe-shutdown-splash.log")

MPE_UNITS = [
    "touch-patch-browser.service",
    "surge-xt-cli.service",
    "mpe-shutdown-splash.service",
    "touch-boot-animation.service",
    "usb-audio-gadget.service",
    "surge-watchdog.service",
    "foot-pedal.service",
    "patch-browser.service",
    "shutdown-animation.service",
]

MILESTONE_UNITS = [
    "shutdown.target",
    "halt.target",
    "reboot.target",
    "systemd-poweroff.service",
    "systemd-reboot.service",
    "systemd-halt.service",
]

def _is_stop_related(message: str) -> bool:
    if message.startswith(("Stopping ", "Stopped ", "Killing ", "Failed")):
        return True
    if "Deactivated successfully" in message:
        return True
    if "Timed out" in message:
        return True
    return False


@dataclass
class JournalLine:
    ts: datetime
    unit: str
    message: str


@dataclass
class StopSpan:
    unit: str
    stopping: datetime | None = None
    stopped: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float | None:
        if self.stopping is None or self.stopped is None:
            return None
        return (self.stopped - self.stopping).total_seconds()


def _journal_json(boot: int, units: list[str] | None = None) -> list[dict]:
    cmd = ["journalctl", "-b", str(boot), "-o", "json", "--no-pager"]
    if units:
        for unit in units:
            cmd.extend(["-u", unit])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: journalctl failed: {exc}", file=sys.stderr)
        return []
    if proc.returncode != 0 and not proc.stdout.strip():
        return []
    entries: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    # journal __REALTIME_TIMESTAMP is microseconds since epoch (string)
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1_000_000)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def _journal_lines(entries: list[dict]) -> list[JournalLine]:
    lines: list[JournalLine] = []
    for entry in entries:
        ts = _parse_ts(str(entry.get("__REALTIME_TIMESTAMP", "")))
        if ts is None:
            continue
        unit = str(entry.get("SYSTEMD_UNIT") or entry.get("_SYSTEMD_UNIT") or "")
        message = str(entry.get("MESSAGE", ""))
        lines.append(JournalLine(ts=ts, unit=unit, message=message))
    lines.sort(key=lambda row: row.ts)
    return lines


def _build_stop_spans(lines: list[JournalLine]) -> list[StopSpan]:
    by_unit: dict[str, StopSpan] = {}
    for row in lines:
        if not _is_stop_related(row.message):
            continue
        span = by_unit.setdefault(row.unit, StopSpan(unit=row.unit))
        if row.message.startswith("Stopping "):
            span.stopping = row.ts
        elif row.message.startswith("Stopped ") or "Deactivated successfully" in row.message:
            span.stopped = row.ts
        elif "Timed out" in row.message or row.message.startswith("Failed"):
            span.notes.append(row.message)
    spans = [s for s in by_unit.values() if s.stopping or s.stopped or s.notes]
    spans.sort(
        key=lambda s: s.stopping or s.stopped or datetime.min,
    )
    return spans


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _load_marker(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _fmt_delta(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{seconds:.2f}s"


def _print_spans(spans: list[StopSpan]) -> None:
    print("=== Unit stop durations (previous boot) ===")
    if not spans:
        print("(no Stopping/Stopped pairs — first boot, logs rotated, or no shutdown recorded)")
        return
    print(f"{'Unit':<40} {'Stop Δ':>8}  Notes")
    print("-" * 72)
    for span in spans:
        notes = "; ".join(span.notes) if span.notes else ""
        print(f"{span.unit:<40} {_fmt_delta(span.duration_s):>8}  {notes}")


def _print_milestones(lines: list[JournalLine]) -> None:
    print("")
    print("=== Shutdown milestones (previous boot) ===")
    if not lines:
        print("(none)")
        return
    for row in lines:
        if row.unit in MILESTONE_UNITS or "Reached target" in row.message:
            print(f"{row.ts.strftime('%H:%M:%S.%f')[:-3]}  {row.unit or 'system'}  {row.message}")


def _print_trace(rows: list[dict], marker: dict | None) -> None:
    print("")
    print("=== App trace (logs/shutdown-trace.jsonl) ===")
    if marker:
        print(
            f"Test marker: method={marker.get('method', '?')} "
            f"marked_at={marker.get('marked_at', '?')} "
            f"note={marker.get('note', '')}"
        )
    if not rows:
        print("(no trace events — upgrade deployed? run shutdown after pulling measure tooling)")
        return
    # Show events from the last shutdown attempt (after last boot if marker present)
    cutoff = marker.get("marked_epoch") if marker else None
    shown = [r for r in rows if cutoff is None or r.get("ts_epoch", 0) >= cutoff]
    if not shown:
        shown = rows[-12:]
    for row in shown[-20:]:
        event = row.get("event", "?")
        ts = row.get("ts_wall", "?")
        extra = {k: v for k, v in row.items() if k not in ("event", "ts_wall", "ts_epoch", "pid")}
        extra_s = " ".join(f"{k}={v}" for k, v in extra.items())
        print(f"  {ts}  {event}  {extra_s}".rstrip())

    if len(shown) >= 2 and shown[0].get("ts_epoch") and shown[-1].get("ts_epoch"):
        wall = shown[-1]["ts_epoch"] - shown[0]["ts_epoch"]
        print(f"  → trace span (first→last event): {wall:.2f}s")


def _print_splash_log() -> None:
    print("")
    print("=== /tmp/mpe-shutdown-splash.log (last 15 lines) ===")
    if not SPLASH_LOG.is_file():
        print(f"(missing {SPLASH_LOG} — tmpfs cleared or splash never started)")
        return
    for line in SPLASH_LOG.read_text(encoding="utf-8").splitlines()[-15:]:
        print(f"  {line}")


def _print_wall_clock(spans: list[StopSpan], milestone_lines: list[JournalLine], marker: dict | None) -> None:
    print("")
    print("=== Wall-clock summary ===")
    if marker:
        print(f"User marked test at epoch {marker.get('marked_epoch')} ({marker.get('marked_at')})")
    stop_starts = [s.stopping for s in spans if s.stopping]
    stop_ends = [s.stopped for s in spans if s.stopped]
    milestones = [ln.ts for ln in milestone_lines if "systemd-poweroff" in ln.unit or "Reached target shutdown" in ln.message]
    if stop_starts:
        t0 = min(stop_starts)
        print(f"First unit 'Stopping' in journal: {t0.strftime('%H:%M:%S.%f')[:-3]}")
    if stop_ends:
        t1 = max(stop_ends)
        print(f"Last unit 'Stopped' in journal:   {t1.strftime('%H:%M:%S.%f')[:-3]}")
        if stop_starts:
            print(f"Journal stop phase span:           {(t1 - min(stop_starts)).total_seconds():.2f}s")
    if milestones:
        print(f"Poweroff milestone:                {milestones[0].strftime('%H:%M:%S.%f')[:-3]}")
    print("")
    print("Interpretation:")
    print("  • Journal stop phase ≈ systemd work (services stopping).")
    print("  • Spinner time ≈ stop phase + sync + driver poweroff (splash holds until cut).")
    print("  • Compare UI vs SSH tests using shutdown-mark-test.sh labels.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure previous-boot shutdown timing")
    parser.add_argument("--boot", type=int, default=-1, help="Journal boot index (default: -1)")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--marker", type=Path, default=DEFAULT_MARKER)
    args = parser.parse_args(argv)

    print(f"Shutdown measurement (journal boot index {args.boot})")
    print("")

    all_units = sorted(set(MPE_UNITS + MILESTONE_UNITS))
    entries = _journal_json(args.boot, all_units)
    if not entries:
        # Fallback: whole previous boot, filter in Python
        entries = _journal_json(args.boot, None)
        if not entries:
            print("ERROR: No journal for previous boot. Run a shutdown test first.")
            return 1

    lines = _journal_lines(entries)
    mpe_lines = [ln for ln in lines if ln.unit in MPE_UNITS or _is_stop_related(ln.message)]
    milestone_lines = [ln for ln in lines if ln.unit in MILESTONE_UNITS or "Reached target" in ln.message]

    spans = _build_stop_spans(mpe_lines)
    _print_spans(spans)
    _print_milestones(milestone_lines)
    _print_trace(_load_jsonl(args.trace), _load_marker(args.marker))
    _print_splash_log()
    _print_wall_clock(spans, milestone_lines, _load_marker(args.marker))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
