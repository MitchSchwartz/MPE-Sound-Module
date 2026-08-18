#!/usr/bin/env python3
"""SooperLooper HUD — bar/beat from SL internal tempo.

Deprecated entry point: Phase 3M merged HUD into mpe-looper-session.service.
Use `python3 scripts/looper-session.py --hud-only` for standalone HUD runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sl_hud_monitor import main

if __name__ == "__main__":
    raise SystemExit(main())
