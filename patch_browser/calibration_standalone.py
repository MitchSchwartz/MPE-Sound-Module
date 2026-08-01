"""Standalone (Sound Blaster) calibration routing — Surge restart + dsnoop capture."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def detect_script_path(repo_root: Path) -> Path:
    return repo_root / "scripts" / "detect-audio-device.sh"


def run_detect_audio_device(
    cli_path: Path,
    *,
    detect_script: Path,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Run detect-audio-device.sh and return DEVICE_ID, DEVICE_NAME, TIER."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    run_env.setdefault("MPE_AUDIO_PROFILE", "standalone")
    result = subprocess.run(
        ["bash", str(detect_script), str(cli_path)],
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
    )
    blob = result.stdout
    if result.returncode != 0:
        raise RuntimeError(
            f"detect-audio-device.sh failed ({result.returncode}): "
            f"{result.stderr or result.stdout}"
        )
    parsed: dict[str, str] = {}
    for line in blob.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key.strip()] = value.strip()
    device_id = parsed.get("DEVICE_ID", "")
    if not device_id:
        raise RuntimeError("detect-audio-device.sh did not return DEVICE_ID")
    return parsed




def parse_surge_sound_blaster_direct_interface(device_list: str) -> str | None:
    """Prefer Direct hardware SB output — pairs with dsnoop capture."""
    for line in device_list.splitlines():
        if "Output Audio Device" not in line:
            continue
        if "sound blaster" not in line.lower():
            continue
        if "direct hardware" not in line.lower():
            continue
        match = re.search(r"\[(\d+\.\d+)\]", line)
        if match:
            return match.group(1)
    return None


def _list_surge_devices(cli_path: Path) -> str:
    result = subprocess.run(
        [str(cli_path), "--list-devices"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr

def resolve_surge_standalone_interface(
    cli_path: Path,
    *,
    detect_script: Path,
) -> str:
    """Surge --audio-interface for standalone calibration (Sound Blaster preferred)."""
    info = run_detect_audio_device(cli_path, detect_script=detect_script)
    tier = info.get("TIER", "")
    if tier in ("3", "4"):
        name = info.get("DEVICE_NAME", "unknown")
        raise RuntimeError(
            f"Standalone calibration requires Sound Blaster USB output, not fallback "
            f"device (tier {tier}: {name})"
        )
    device_id = info["DEVICE_ID"]
    if not _interface_is_sound_blaster(cli_path, device_id):
        raise RuntimeError(
            f"Standalone calibration expected Sound Blaster interface, got {device_id} "
            f"({info.get('DEVICE_NAME', '')})"
        )
    direct = parse_surge_sound_blaster_direct_interface(_list_surge_devices(cli_path))
    if direct:
        return direct
    return device_id


def _interface_is_sound_blaster(cli_path: Path, device_id: str) -> bool:
    result = subprocess.run(
        [str(cli_path), "--list-devices"],
        capture_output=True,
        text=True,
        check=False,
    )
    blob = result.stdout + result.stderr
    for line in blob.splitlines():
        if f"[{device_id}]" not in line or "Output Audio Device" not in line:
            continue
        if "sound blaster" in line.lower():
            return True
    return False


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


def resolve_standalone_capture_device(
    *,
    cards_text: str | None = None,
    arecord_list: str | None = None,
) -> str | None:
    """Capture PCM that snoops Sound Blaster playback (dsnoop), not raw ADC input."""
    cards = cards_text
    if cards is None:
        try:
            cards = Path("/proc/asound/cards").read_text()
        except OSError:
            cards = ""
    card_id = _sound_blaster_card_id(cards)
    if not card_id:
        return None

    if arecord_list is None:
        try:
            result = subprocess.run(
                ["arecord", "-L"],
                capture_output=True,
                text=True,
                check=False,
            )
            arecord_list = result.stdout
        except OSError:
            arecord_list = ""

    needle = f"dsnoop:CARD={card_id},DEV=0"
    if needle in arecord_list:
        return needle

    for line in arecord_list.splitlines():
        if line.startswith("dsnoop:") and f"CARD={card_id}" in line:
            return line.strip()

    return f"plughw:CARD={card_id},DEV=0"


def should_restart_surge_for_standalone(
    *,
    use_loopback: bool,
    dry_run: bool,
    mock_lufs: float | None,
) -> bool:
    if use_loopback or dry_run or mock_lufs is not None:
        return False
    profile = os.environ.get("MPE_AUDIO_PROFILE", "standalone").strip().lower()
    return profile == "standalone"
