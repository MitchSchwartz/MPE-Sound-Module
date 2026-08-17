"""Session control plane event stream — Phase 2 (spec criterion 9).

Append-only JSONL ring buffer under ``/run/mpe/events.jsonl``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

EVENT_NAMES = frozenset(
    {
        "engine.started",
        "engine.exited",
        "grid.established",
        "grid.dropped",
        "buffer.changed",
        "client.registered",
        "client.leaked",
        "mode.changed",
        "looper.units.stopped",
        "looper.units.started",
    }
)

EVENTS_FILENAME = "events.jsonl"
MAX_EVENTS = 2000


def run_dir() -> Path:
    return Path(os.environ.get("MPE_RUN_DIR", "/run/mpe"))


def events_path(*, run: Path | None = None) -> Path:
    return (run or run_dir()) / EVENTS_FILENAME


def format_event(
    name: str,
    *,
    detail: str = "",
    source: str = "",
    fields: dict[str, Any] | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    if name not in EVENT_NAMES:
        raise ValueError(f"unknown event name: {name}")
    payload: dict[str, Any] = {
        "ts": time.time() if ts is None else ts,
        "event": name,
        "source": source or "unknown",
    }
    if detail:
        payload["detail"] = detail
    if fields:
        payload.update(fields)
    return payload


def event_line(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def parse_event_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or "event" not in raw:
        return None
    return raw


def trim_ring_buffer(lines: list[str], *, max_events: int = MAX_EVENTS) -> list[str]:
    if len(lines) <= max_events:
        return lines
    return lines[-max_events:]


def emit_event(
    name: str,
    *,
    detail: str = "",
    source: str = "",
    fields: dict[str, Any] | None = None,
    ts: float | None = None,
    run: Path | None = None,
    max_events: int = MAX_EVENTS,
) -> dict[str, Any]:
    """Append one structured event; rotate file when over ``max_events``."""
    event = format_event(name, detail=detail, source=source, fields=fields, ts=ts)
    path = events_path(run=run)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[str] = []
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []

    existing.append(event_line(event))
    existing = trim_ring_buffer(existing, max_events=max_events)

    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(existing) + ("\n" if existing else ""), encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(path)
    return event


def read_events(
    path: Path | None = None,
    *,
    run: Path | None = None,
    limit: int | None = None,
    name: str | None = None,
) -> list[dict[str, Any]]:
    target = path or events_path(run=run)
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        parsed = parse_event_line(line)
        if parsed is None:
            continue
        if name is not None and parsed.get("event") != name:
            continue
        out.append(parsed)
    if limit is not None and limit >= 0:
        out = out[-limit:]
    return out
