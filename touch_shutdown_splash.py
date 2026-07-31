#!/usr/bin/env python3
"""Shutdown DSI splash for touch mode (systemd + standalone)."""

from __future__ import annotations

import sys

from patch_browser.dsi_splash import run_shutdown_animation


def main() -> int:
    run_shutdown_animation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
