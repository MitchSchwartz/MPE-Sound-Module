#!/usr/bin/env python3
"""Keep Surge global output limiter in sync with touch settings."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.surge_monitor import SurgeMonitor  # noqa: E402
from patch_browser.surge_output_limiter import sync_output_limiter  # noqa: E402
from patch_browser.touch_ui_constants import UI_STATE_FILE


def main() -> int:
    try:
        from pythonosc import udp_client
    except ImportError:
        print("Error: python-osc required for surge-output-limiter", file=sys.stderr)
        return 1

    monitor = SurgeMonitor()
    osc = udp_client.SimpleUDPClient("127.0.0.1", 53280)
    prefs_mtime = 0.0
    print("Surge output limiter sync running.", flush=True)
    sync_output_limiter(osc)
    try:
        prefs_mtime = UI_STATE_FILE.stat().st_mtime
    except OSError:
        pass
    while True:
        try:
            healthy, _ = monitor.check_health()
            if healthy:
                try:
                    stat = UI_STATE_FILE.stat()
                    if stat.st_mtime > prefs_mtime:
                        prefs_mtime = stat.st_mtime
                        sync_output_limiter(osc)
                except OSError:
                    pass
        except Exception as exc:
            print(f"Surge output limiter sync error: {exc}", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
