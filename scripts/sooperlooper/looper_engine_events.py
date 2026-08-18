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

from patch_browser.session_events import read_events  # noqa: E402

LOOPER_ENGINE_STARTED = "looper.engine.started"


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

    def poll(self) -> None:
        events = read_events(name=LOOPER_ENGINE_STARTED, run=self._run)
        if not events:
            return
        latest = events[-1]
        ts = float(latest.get("ts") or 0.0)
        if not self._bootstrapped:
            self._last_ts = ts
            self._bootstrapped = True
            return
        if ts > self._last_ts:
            self._last_ts = ts
            self._on_restart()


def poll_interval_s() -> float:
    return float(os.environ.get("MPE_SL_ENGINE_EVENT_POLL_S", "1.0"))
