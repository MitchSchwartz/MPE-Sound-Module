"""ALSA device resolution for the on-device audio looper."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from patch_browser.calibration_loopback import (
    LOOPBACK_CAPTURE_SUBSTREAM,
    ensure_snd_aloop,
    resolve_loopback_capture_device,
)
from patch_browser.session_capture import _sound_blaster_card_id


def _read_cards() -> str:
    try:
        return Path("/proc/asound/cards").read_text(encoding="utf-8")
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


def resolve_sound_blaster_playback_device(
    *,
    cards_text: str | None = None,
    aplay_list: str | None = None,
) -> str | None:
    """DAC output on the Sound Blaster Play! 3 (standalone default)."""
    cards = cards_text if cards_text is not None else _read_cards()
    card_id = _sound_blaster_card_id(cards)
    if not card_id:
        return None

    listing = aplay_list if aplay_list is not None else _aplay_list()
    hw_dev: str | None = None
    plug_dev: str | None = None
    dsnoop_dev: str | None = None
    for line in listing.splitlines():
        stripped = line.strip()
        if f"CARD={card_id}" not in stripped or "DEV=0" not in stripped:
            continue
        if stripped.startswith("plughw:"):
            plug_dev = stripped
        elif stripped.startswith("hw:"):
            hw_dev = stripped
        elif stripped.startswith("dsnoop:"):
            dsnoop_dev = stripped
    return plug_dev or hw_dev or dsnoop_dev or f"plughw:CARD={card_id},DEV=0"


def resolve_looper_capture_device(*, cards_text: str | None = None) -> str:
    """Surge loopback tap (substream 1 captures playback on substream 0)."""
    return resolve_loopback_capture_device(cards_text=cards_text)


def prepare_looper_audio_path(*, load_loopback: bool = True) -> tuple[str, str]:
    """Return (capture_device, playback_device), optionally loading snd-aloop."""
    if load_loopback:
        ensure_snd_aloop()
    cards = _read_cards()
    capture = resolve_looper_capture_device(cards_text=cards)
    playback = resolve_sound_blaster_playback_device(cards_text=cards)
    if playback is None:
        raise RuntimeError(
            "No Sound Blaster playback device (standalone looper expects Tier-1 DAC)"
        )
    return capture, playback


def surge_loopback_hint() -> str:
    """Operator note: Surge must output to Loopback for capture to carry signal."""
    sub = LOOPBACK_CAPTURE_SUBSTREAM.replace(",", ", subdevice ")
    return (
        "Surge must use Loopback as its ALSA output (not Sound Blaster direct). "
        f"Capture reads loopback {sub}. "
        "For Phase 0: stop surge-xt-cli, load snd-aloop, point Surge at Loopback, "
        "then run this spike while listening on the Sound Blaster."
    )
