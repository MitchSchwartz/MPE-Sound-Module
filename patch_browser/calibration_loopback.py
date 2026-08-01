"""ALSA loopback setup for patch normalization calibration."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

LOOPBACK_CARD_NAME = "Loopback"
# Substream 1 captures playback routed through loopback substream 0.
LOOPBACK_CAPTURE_SUBSTREAM = "1,0"


def loopback_card_line(cards_text: str) -> str | None:
    for line in cards_text.splitlines():
        if re.search(r"^\s*\d+\s+\[Loopback", line):
            return line.strip()
    return None


def loopback_card_number(cards_text: str) -> int | None:
    line = loopback_card_line(cards_text)
    if not line:
        return None
    match = re.match(r"\s*(\d+)", line)
    return int(match.group(1)) if match else None


def read_asound_cards() -> str:
    return Path("/proc/asound/cards").read_text()


def ensure_snd_aloop(*, timeout_s: float = 5.0) -> None:
    """Load snd-aloop and wait until the Loopback card appears."""
    subprocess.run(["sudo", "modprobe", "snd-aloop"], check=False)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if loopback_card_line(read_asound_cards()):
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError(
        "snd-aloop did not appear after modprobe (try: sudo modprobe snd-aloop)"
    )


def resolve_loopback_capture_device(*, cards_text: str | None = None) -> str:
    """Return plughw capture device for Surge loopback monitoring."""
    text = cards_text if cards_text is not None else read_asound_cards()
    card = loopback_card_number(text)
    if card is not None:
        return f"plughw:{card},{LOOPBACK_CAPTURE_SUBSTREAM}"
    return f"plughw:{LOOPBACK_CARD_NAME},{LOOPBACK_CAPTURE_SUBSTREAM}"


def parse_surge_loopback_interface(device_list: str) -> str:
    """Pick Surge ``--audio-interface`` ID for loopback Direct hardware output."""
    for line in device_list.splitlines():
        if "Output Audio Device" not in line or "Loopback" not in line:
            continue
        if "Direct sample mixing" in line:
            continue
        match = re.search(r"\[(\d+\.\d+)\]", line)
        if match:
            return match.group(1)
    raise RuntimeError(
        "No Loopback output in surge --list-devices (is snd-aloop loaded?)"
    )


def resolve_surge_loopback_interface(cli_path: Path) -> str:
    result = subprocess.run(
        [str(cli_path), "--list-devices"],
        capture_output=True,
        text=True,
        check=False,
    )
    blob = result.stdout + result.stderr
    try:
        return parse_surge_loopback_interface(blob)
    except RuntimeError:
        card = loopback_card_number(read_asound_cards())
        if card is not None:
            # Surge --list-devices sometimes omits Loopback until a client opens PCM.
            return f"{card}.0"
        raise RuntimeError(
            "No Loopback output in surge --list-devices and no Loopback ALSA card"
        ) from None
