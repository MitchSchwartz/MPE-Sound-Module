"""Screen and navigation enums for the touch patch browser."""

from __future__ import annotations

import os
from enum import Enum, auto


class Screen(Enum):
    BROWSER = auto()
    SETTINGS = auto()
    THEME = auto()
    CALIBRATE_CONFIRM = auto()
    POWER_MENU = auto()
    POWER_CONFIRM = auto()


class CalibrateMode(Enum):
    """Which normalization calibration scope to run from System settings."""

    MISSING_ONLY = auto()
    FORCE_FULL = auto()


class LeftNavMode(Enum):
    FOLDERS = auto()
    PATCHES = auto()


def audio_profile_display() -> str:
    profile = os.environ.get("MPE_AUDIO_PROFILE", "standalone").strip().lower()
    if profile == "usb-host":
        return "Audio profile: USB host (gadget)"
    return "Audio profile: Analog (standalone)"
