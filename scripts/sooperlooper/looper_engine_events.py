"""Explicit looper engine lifecycle via session events (criterion 40).

Replaces config-drift sentinels: ``looper.engine.started`` is emitted when
``wire-sooperlooper-graph.sh`` verifies the graph — an explicit fact, not inference.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from patch_browser.session_events import (  # noqa: E402
    events_path,
    parse_event_line,
)

LOOPER_ENGINE_STARTED = "looper.engine.started"

# Latest event is always appended; tail read avoids scanning a full 2000-line ring.
_TAIL_READ_BYTES = 65536


def _file_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
        return (st.st_dev, st.st_ino, st.st_size)
    except OSError:
        return None


def _latest_looper_started_ts(path: Path) -> float | None:
    """Return ts of the newest ``looper.engine.started`` line, reading from the tail."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    read_len = min(size, _TAIL_READ_BYTES)
    try:
        with path.open("rb") as handle:
            handle.seek(size - read_len)
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    latest: float | None = None
    for line in chunk.splitlines():
        parsed = parse_event_line(line)
        if parsed is None or parsed.get("event") != LOOPER_ENGINE_STARTED:
            continue
        latest = float(parsed.get("ts") or 0.0)
    return latest


class LooperEngineEventWatch:
    """Fire when a new ``looper.engine.started`` appears after bootstrap."""

    def __init__(
        self,
        on_restart: Callable[[], None],
        *,
        run: Path | None = None,
    ) -> None:
        self._on_restart = on_restart
        self._run = run
        self._last_ts = 0.0
        self._bootstrapped = False
        self._file_sig: tuple[int, int, int] | None = None

    def _events_path(self) -> Path:
        return events_path(run=self._run)

    def poll(self) -> None:
        path = self._events_path()
        sig = _file_signature(path)
        if sig is not None and sig == self._file_sig:
            return
        self._file_sig = sig
        if sig is None:
            return

        ts = _latest_looper_started_ts(path)
        if ts is None:
            return
        if not self._bootstrapped:
            self._last_ts = ts
            self._bootstrapped = True
            return
        if ts > self._last_ts:
            self._last_ts = ts
            self._on_restart()


def poll_interval_s() -> float:
    return float(os.environ.get("MPE_SL_ENGINE_EVENT_POLL_S", "1.0"))
