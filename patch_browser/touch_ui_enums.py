"""Screen and navigation enums for the touch patch browser."""

from __future__ import annotations

from enum import Enum, auto


class Screen(Enum):
    BROWSER = auto()
    SETTINGS = auto()
    THEME = auto()
    CALIBRATE_CONFIRM = auto()
    POWER_MENU = auto()
    POWER_CONFIRM = auto()
    SURGE_BUFFER_MODAL = auto()
    SURGE_SAMPLE_RATE_MODAL = auto()
    AUDIO_PROFILE_MODAL = auto()
    BRIGHTNESS_MODAL = auto()
    WIFI_MODAL = auto()
    MIDI_SYNC_MODAL = auto()
    CONTEXT_MENU = auto()
    NAME_PROMPT = auto()
    LOOPER_CONFIRM = auto()
    LOOPER_NAME = auto()


class CalibrateMode(Enum):
    """Which normalization calibration scope to run from System settings."""

    MISSING_ONLY = auto()
    FORCE_FULL = auto()


class LeftNavMode(Enum):
    FOLDERS = auto()
    PATCHES = auto()
    ALL_PATCHES = auto()


def audio_profile_display() -> str:
    from patch_browser.audio_profile import profile_settings_label

    return profile_settings_label()
