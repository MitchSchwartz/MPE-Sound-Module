#!/usr/bin/env python3
"""Early-boot DSI splash for touch mode (systemd touch-boot-animation.service)."""

from __future__ import annotations

import argparse
import sys

from patch_browser.dsi_splash import (
    paint_hold_black,
    run_boot_animation,
    run_hold_loop,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Touch DSI boot splash")
    parser.add_argument(
        "--mode",
        choices=("hold", "animate", "black"),
        default="hold",
        help="hold=until stopped by browser start; animate=fixed duration; black=instant fill",
    )
    parser.add_argument("--duration", type=float, default=None, help="Seconds for --mode animate")
    parser.add_argument(
        "--no-debounce",
        action="store_true",
        help="Always run full animation (ignore fast-restart debounce)",
    )
    args = parser.parse_args()

    if args.mode == "black":
        paint_hold_black()
        return 0
    if args.mode == "animate":
        run_boot_animation(duration=args.duration, debounce=not args.no_debounce)
        return 0
    run_hold_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
