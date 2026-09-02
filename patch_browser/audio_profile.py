"""MPE audio output profile — standalone, usb-host, usb-host-session."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

VALID_PROFILES = frozenset({"standalone", "usb-host", "usb-host-session"})
PROFILE_OPTIONS: tuple[tuple[str, str, str], ...] = (
    # "USB DAC", not a product name. The appliance binds whichever USB DAC is
    # present (tier 1 or 2) -- a Scarlett 4i4 and a KM-HIFI-384KHZ have both run
    # this profile. Naming one product in the label told the user the wrong
    # device was selected. detect-audio-device.sh already says "USB DAC" here.
    ("standalone", "Analog", "USB DAC — headphones and pedal"),
    ("usb-host", "USB direct", "Surge to PC when recording (analog mutes)"),
    ("usb-host-session", "USB session", "Analog stays on; mic return to PC"),
)

# PROFILE_OPTIONS above is the VOCABULARY -- every profile the appliance
# understands, with its label. HIDDEN_PROFILES is about the MENU only. Removing
# a row from the vocabulary would make profile_option_label() fall through to
# "Analog", so an appliance sitting on a hidden profile would be mislabelled as
# something else -- the failure this whole area keeps producing.
#
# usb-host-session ("mic return to PC") is hidden because the mic capture it
# depends on is hardwired to a Sound Blaster product string that is not on this
# appliance; see issue #136 for what has to be true before it comes back.
HIDDEN_PROFILES: frozenset[str] = frozenset({"usb-host-session"})


def menu_profile_options(current: str | None = None) -> tuple[tuple[str, str, str], ...]:
    """Rows the settings modal offers.

    A hidden profile is still shown when it is the ACTIVE one -- otherwise the
    menu would not name the state the appliance is in, and there would be no way
    to switch off it. Same rule as a saved-but-absent audio device.
    """
    active = normalize_profile(current if current is not None else current_profile())
    return tuple(
        option for option in PROFILE_OPTIONS
        if option[0] not in HIDDEN_PROFILES or option[0] == active
    )
def profile_env_path() -> Path | None:
    """Appliance canon file, or None when MPE_ENV_FILE='' (hermetic tests)."""
    if "MPE_ENV_FILE" in os.environ:
        raw = os.environ["MPE_ENV_FILE"].strip()
        return Path(raw) if raw else None
    return Path("/etc/mpe/mpe.env")


MPE_ENV_PATH = Path("/etc/mpe/mpe.env")
SET_PROFILE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set-audio-profile.sh"
# Gadget wait (8s) + Surge restart without MIDI wait (~5–8s) + margin
PROFILE_SWITCH_TIMEOUT_S = 45.0


def normalize_profile(value: str | None) -> str:
    profile = (value or "standalone").strip().lower()
    if profile not in VALID_PROFILES:
        return "standalone"
    return profile


def profile_option_label(profile: str) -> str:
    normalized = normalize_profile(profile)
    for key, label, _hint in PROFILE_OPTIONS:
        if key == normalized:
            return label
    return "Analog"


def profile_settings_label() -> str:
    return profile_option_label(current_profile())


def profile_switch_overlay_hint(profile: str) -> str:
    normalized = normalize_profile(profile)
    if normalized == "standalone":
        return "Stopping USB gadget and restarting Surge"
    if normalized == "usb-host":
        return "Starting USB gadget and restarting Surge"
    if normalized == "usb-host-session":
        return "Session record route — Surge on analog, mic to USB"
    return "Restarting Surge for new audio route"


def current_profile() -> str:
    path = profile_env_path()
    if path is None:
        return normalize_profile(os.environ.get("MPE_AUDIO_PROFILE"))
    from_file = read_profile_from_env_file(path)
    if from_file is not None:
        return from_file
    return normalize_profile(os.environ.get("MPE_AUDIO_PROFILE"))


def is_usb_host() -> bool:
    return current_profile() == "usb-host"


def is_usb_tethered() -> bool:
    return current_profile() in ("usb-host", "usb-host-session")


def header_badge_label() -> str:
    if is_usb_tethered():
        from patch_browser.usb_audio_recovery import is_recovering

        if is_recovering():
            return "Sync"
        return "USB"
    return "Analog"


def settings_toggle_label() -> str:
    return "Audio output"


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
    if profile == "usb-host-session":
        return True, "Session record — mic → USB when PC captures"
    return True, "Analog audio (USB DAC)"


def read_profile_from_env_file(path: Path = MPE_ENV_PATH) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*MPE_AUDIO_PROFILE\s*=\s*(.+?)\s*$", line)
        if match:
            return normalize_profile(match.group(1).strip().strip('"').strip("'"))
    return None
