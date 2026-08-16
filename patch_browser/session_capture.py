"""Sound Blaster mic capture → UAC2 gadget (loop session record to host PC)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _sound_blaster_card_id(cards_text: str) -> str | None:
    for line in cards_text.splitlines():
        match = re.match(r"\s*\d+\s+\[([^\]]+)\].*Sound Blaster", line, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    for line in cards_text.splitlines():
        if "Sound Blaster" in line:
            match = re.match(r"\s*\d+\s+\[([^\]]+)\]", line)
            if match:
                return match.group(1).strip()
    return None


def _uac2_card_id(cards_text: str) -> str | None:
    for line in cards_text.splitlines():
        if not re.search(r"UAC2|USB Audio Passthrough|MPE Sound Module", line, re.I):
            continue
        match = re.match(r"\s*(\d+)\s+\[([^\]]+)\]", line)
        if match:
            return match.group(2).strip()
    return None


def _read_cards() -> str:
    try:
        return Path("/proc/asound/cards").read_text(encoding="utf-8")
    except OSError:
        return ""


def _arecord_list() -> str:
    try:
        result = subprocess.run(
            ["arecord", "-L"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout
    except OSError:
        return ""


def _aplay_list() -> str:
    try:
        result = subprocess.run(
            ["aplay", "-L"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout
    except OSError:
        return ""


def resolve_blaster_mic_capture_device(
    *,
    cards_text: str | None = None,
    arecord_list: str | None = None,
) -> str | None:
    """Raw ADC from Sound Blaster mic in (RC-5 return), not dsnoop."""
    cards = cards_text if cards_text is not None else _read_cards()
    card_id = _sound_blaster_card_id(cards)
    if not card_id:
        return None

    listing = arecord_list if arecord_list is not None else _arecord_list()
    hw_dev: str | None = None
    plug_dev: str | None = None
    for line in listing.splitlines():
        stripped = line.strip()
        if f"CARD={card_id}" not in stripped or "DEV=0" not in stripped:
            continue
        if stripped.startswith("plughw:"):
            plug_dev = stripped
        elif stripped.startswith("hw:"):
            hw_dev = stripped
    # plughw accepts mono; hw on Sound Blaster Play! 3 is stereo-only.
    return plug_dev or hw_dev or f"plughw:CARD={card_id},DEV=0"


def resolve_uac2_playback_device(
    *,
    cards_text: str | None = None,
    aplay_list: str | None = None,
) -> str | None:
    """UAC2 gadget playback PCM — host PC capture side."""
    cards = cards_text if cards_text is not None else _read_cards()
    card_id = _uac2_card_id(cards)
    if not card_id:
        return None

    listing = aplay_list if aplay_list is not None else _aplay_list()
    for line in listing.splitlines():
        stripped = line.strip()
        if not stripped.startswith("hw:"):
            continue
        if f"CARD={card_id}" in stripped and "DEV=0" in stripped:
            return stripped
    return f"plughw:CARD={card_id},DEV=0"
