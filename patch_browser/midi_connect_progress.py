"""ROLI / MIDI keyboard hot-plug progress written by roli-connect-debounce.sh."""

from __future__ import annotations

import os
import time
from pathlib import Path

CONNECT_STATE_PATH = Path(
    os.environ.get("MPE_MIDI_CONNECT_STATE", "/run/mpe/midi-connect.state")
)
COOLDOWN_STATE_PATH = Path(
    os.environ.get(
        "MPE_MIDI_HOTPLUG_COOLDOWN",
        str(CONNECT_STATE_PATH.parent / "midi-hotplug-cooldown"),
    )
)
STALE_SECONDS = 30.0
HOTPLUG_COOLDOWN_S = float(os.environ.get("MPE_MIDI_HOTPLUG_COOLDOWN_S", "20"))


def _read_state_text() -> str | None:
    if not CONNECT_STATE_PATH.is_file():
        return None
    try:
        return CONNECT_STATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_since(text: str, *, path: Path) -> float:
    parts = text.split()
    if len(parts) >= 2:
        try:
            return float(parts[1])
        except ValueError:
            pass
    return path.stat().st_mtime


def _state_fresh(text: str, prefix: str) -> bool:
    if not text.startswith(prefix):
        return False
    since = _parse_since(text, path=CONNECT_STATE_PATH)
    return (time.time() - since) < STALE_SECONDS


def _cooldown_age_s() -> float | None:
    if not COOLDOWN_STATE_PATH.is_file():
        return None
    try:
        raw = COOLDOWN_STATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    parts = raw.split()
    if len(parts) >= 2:
        try:
            since = float(parts[1])
        except ValueError:
            since = COOLDOWN_STATE_PATH.stat().st_mtime
    else:
        try:
            since = float(raw)
        except ValueError:
            since = COOLDOWN_STATE_PATH.stat().st_mtime
    age = time.time() - since
    return age if age >= 0 else None


def hotplug_cooldown_active() -> bool:
    age = _cooldown_age_s()
    return age is not None and age < HOTPLUG_COOLDOWN_S


def is_connecting() -> bool:
    """True while udev debounce is wiring the pressure remapper to a new controller."""
    text = _read_state_text()
    if not text:
        return False
    return _state_fresh(text, "connecting")


def is_disconnecting() -> bool:
    """True while udev debounce is restarting the remapper after unplug."""
    text = _read_state_text()
    if not text:
        return False
    return _state_fresh(text, "disconnecting")


def blocks_audio_recovery_toast() -> bool:
    """MIDI hot-plug window — keyboard toast only, or silence on unplug."""
    return is_connecting() or is_disconnecting() or hotplug_cooldown_active()


def suppress_audio_recovery_toast() -> bool:
    return blocks_audio_recovery_toast()


def connecting_toast() -> str | None:
    base = connecting_toast_base()
    return f"{base}…" if base else None


def connecting_toast_base() -> str | None:
    if is_connecting():
        return "Connecting keyboard"
    return None
