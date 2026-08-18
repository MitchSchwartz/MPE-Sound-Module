"""Session control plane event stream — Phase 2 (spec criterion 9).

Append-only JSONL ring buffer under ``/run/mpe/events.jsonl``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from patch_browser.mpe_run_dir import run_dir

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
        "looper.engine.started",
    }
)

EVENTS_FILENAME = "events.jsonl"
MAX_EVENTS = 2000


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


def _maybe_rotate_events(path: Path, *, max_events: int) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= max_events:
        return
    trimmed = trim_ring_buffer(lines, max_events=max_events)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(trimmed) + ("\n" if trimmed else ""))
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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

    line = event_line(event) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)

    _maybe_rotate_events(path, max_events=max_events)
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
    if limit is not None:
        if limit <= 0:
            return []
        out = out[-limit:]
    return out
