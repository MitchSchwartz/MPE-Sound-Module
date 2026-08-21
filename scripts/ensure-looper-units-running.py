#!/usr/bin/env python3
"""Start stopped looper units unless maintenance mode is active (calibration recovery)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.calibration_teardown import ensure_looper_units_running


def main() -> int:
    ensure_looper_units_running()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
