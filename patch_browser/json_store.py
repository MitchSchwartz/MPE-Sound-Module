"""Atomic JSON read/write helpers for patch browser state files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json_dict(path: Path, *, label: str | None = None) -> dict[str, Any]:
    """Load a JSON object from disk; return {} on missing or invalid files."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        # Absent is the normal state for request/handoff files that only
        # exist between a writer and its reader. Warning here put ~419
        # journald writes per second on the appliance's hot path (measured
        # 2026-08-28: 25,127 messages in one minute from the remapper).
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        name = label or path.name
        print(f"Warning: could not load {name} {path}: {exc}")
        return {}
    return raw if isinstance(raw, dict) else {}


def atomic_write_json(path: Path, data: dict[str, Any], *, sort_keys: bool = True) -> None:
    """Persist a JSON object atomically so interrupt mid-write cannot truncate the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=sort_keys) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
