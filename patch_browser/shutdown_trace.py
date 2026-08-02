"""Persistent shutdown trace events (JSONL) for post-reboot measurement.

Writes under ``$MPE_MODULE_REPO/logs/shutdown-trace.jsonl`` so events survive
reboot (unlike ``/tmp/mpe-shutdown-splash.log`` on tmpfs-only images).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACE_PATH = Path(
    os.environ.get(
        "MPE_SHUTDOWN_TRACE_PATH",
        str(REPO_ROOT / "logs" / "shutdown-trace.jsonl"),
    )
)


def log_shutdown_event(event: str, **fields: Any) -> None:
    """Append one JSON line; never raises (shutdown path must stay safe)."""
    payload: dict[str, Any] = {
        "ts_wall": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "ts_epoch": round(time.time(), 3),
        "event": event,
        "pid": os.getpid(),
    }
    payload.update(fields)
    try:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass


def systemd_shutdown_pending() -> bool:
    """True when systemd has already entered a halt/poweroff/reboot transaction."""
    if Path("/run/systemd/shutdown/scheduled").exists():
        return True
    try:
        result = __import__("subprocess").run(
            ["systemctl", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        state = (result.stdout or "").strip()
        return state in ("stopping", "maintenance", "degraded")
    except (OSError, __import__("subprocess").TimeoutExpired):
        return False
