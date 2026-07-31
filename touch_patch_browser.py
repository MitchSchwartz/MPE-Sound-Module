#!/usr/bin/env python3
"""
Pi-Surge-MPE Touch Patch Browser

Fullscreen touch UI for ~5" landscape displays (SmartiPi case + panel, DSI or HDMI).
Default layout target: 800×480 landscape — most common 5" panel size.
"""

from patch_browser.touch_browser_app import TouchPatchBrowser, main

__all__ = ["TouchPatchBrowser", "main"]

if __name__ == "__main__":
    main()
