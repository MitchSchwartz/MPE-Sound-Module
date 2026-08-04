#!/usr/bin/env python3
"""Shutdown DSI splash for touch mode (systemd + standalone)."""

from __future__ import annotations

import argparse
import sys

from patch_browser.dsi_splash import hold_shutdown_frame, run_shutdown_animation
from patch_browser.shutdown_trace import log_shutdown_event, shutdown_splash_disabled


def main() -> int:
    parser = argparse.ArgumentParser(description="Touch DSI shutdown splash")
    parser.add_argument(
        "--hold",
        action="store_true",
        help="Hold shutdown frame until systemd kills this unit (halt/reboot path)",
    )
    args = parser.parse_args()
    if shutdown_splash_disabled():
        log_shutdown_event(
            "shutdown_splash_unit_skipped",
            hold=args.hold,
            reason="MPE_SHUTDOWN_SKIP_SPLASH",
        )
        return 0
    if args.hold:
        hold_shutdown_frame()
    else:
        run_shutdown_animation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
