"""ROLI / MIDI keyboard hot-plug progress written by roli-connect-debounce.sh."""

from __future__ import annotations

import os
import time
from pathlib import Path

CONNECT_STATE_PATH = Path(
    os.environ.get("MPE_MIDI_CONNECT_STATE", "/run/mpe/midi-connect.state")
)
STALE_SECONDS = 30.0


def _parse_since(text: str) -> float:
    parts = text.split()
    if len(parts) >= 2:
        try:
            return float(parts[1])
        except ValueError:
            pass
    return CONNECT_STATE_PATH.stat().st_mtime


def is_connecting() -> bool:
    """True while udev debounce is wiring the pressure remapper to a new controller."""
    if not CONNECT_STATE_PATH.is_file():
        return False
    try:
        text = CONNECT_STATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not text.startswith("connecting"):
        return False
    since = _parse_since(text)
    return (time.time() - since) < STALE_SECONDS


def connecting_toast() -> str | None:
    if is_connecting():
        return "Connecting keyboard…"
    return None
