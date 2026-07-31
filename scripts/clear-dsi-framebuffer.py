#!/usr/bin/env python3
"""Fill the DSI/KMS framebuffer before touch-patch-browser starts.

The DRM driver keeps the last rendered frame when pygame exits or the service
stops. Without clearing, a previous UI (e.g. an old build) can flash until the
browser's first flip().
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

try:
    import pygame
except ImportError:
    sys.exit(0)


def main() -> None:
    if os.environ.get("MPE_TOUCH_WINDOWED") == "1" or os.environ.get("DISPLAY"):
        return

    if not os.environ.get("SDL_VIDEODRIVER"):
        os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
        repo = SCRIPT_DIR.parent
        detect = repo / "scripts" / "lib" / "detect-drm-card.sh"
        if detect.is_file():
            import subprocess

            try:
                card = subprocess.check_output(["bash", str(detect)], text=True).strip()
                if card:
                    os.environ["SDL_KMSDRM_DEVICE"] = card
            except (subprocess.CalledProcessError, OSError):
                pass
        os.environ.setdefault("SDL_KMSDRM_REQUIRE_DRM_MASTER", "1")
        os.environ.setdefault("SDL_VIDEO_EGL", "0")

    pygame.init()
    try:
        screen = pygame.display.set_mode((800, 480), pygame.FULLSCREEN)
        screen.fill((10, 10, 12))
        pygame.display.flip()
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
