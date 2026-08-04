"""MPE audio output profile — standalone (Sound Blaster) vs usb-host (UAC2 gadget)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

VALID_PROFILES = frozenset({"standalone", "usb-host"})
MPE_ENV_PATH = Path("/etc/mpe/mpe.env")
SET_PROFILE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set-audio-profile.sh"
# Gadget wait (8s) + Surge restart without MIDI wait (~5–8s) + margin
PROFILE_SWITCH_TIMEOUT_S = 45.0


def normalize_profile(value: str | None) -> str:
    profile = (value or "standalone").strip().lower()
    if profile not in VALID_PROFILES:
        return "standalone"
    return profile


def current_profile() -> str:
    return normalize_profile(os.environ.get("MPE_AUDIO_PROFILE"))


def is_usb_host() -> bool:
    return current_profile() == "usb-host"


def header_badge_label() -> str:
    if is_usb_host():
        from patch_browser.usb_audio_recovery import is_recovering

        if is_recovering():
            return "Sync"
        return "USB"
    return "Analog"


def settings_toggle_label() -> str:
    return "USB Audio"


def settings_toggle_on() -> bool:
    return is_usb_host()


def apply_profile(profile: str) -> tuple[bool, str]:
    """Persist profile, toggle gadget service, restart Surge. Requires sudo script on Pi."""
    profile = normalize_profile(profile)
    if profile not in VALID_PROFILES:
        return False, f"Invalid profile: {profile}"

    if not SET_PROFILE_SCRIPT.is_file():
        return False, "set-audio-profile.sh missing"

    try:
        result = subprocess.run(
            ["sudo", str(SET_PROFILE_SCRIPT), profile],
            capture_output=True,
            text=True,
            timeout=PROFILE_SWITCH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"Profile switch timed out ({int(PROFILE_SWITCH_TIMEOUT_S)}s)"
    except OSError as exc:
        return False, str(exc)[:40]

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "switch failed").strip()
        return False, detail.splitlines()[0][:60]

    os.environ["MPE_AUDIO_PROFILE"] = profile
    if profile == "usb-host":
        return True, "USB host audio — plug USB-C to PC"
    return True, "Analog audio (Sound Blaster)"


def read_profile_from_env_file(path: Path = MPE_ENV_PATH) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*MPE_AUDIO_PROFILE\s*=\s*(.+?)\s*$", line)
        if match:
            return normalize_profile(match.group(1).strip().strip('"').strip("'"))
    return None
