#!/usr/bin/env python3
"""Merged looper session — APC bench + HUD writer (Phase 3M)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sooperlooper"))

from looper_session import run_session  # noqa: E402


if __name__ == "__main__":
    try:
        raise SystemExit(run_session())
    except KeyboardInterrupt:
        raise SystemExit(0)
