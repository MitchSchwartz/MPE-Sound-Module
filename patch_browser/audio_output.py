"""Audio output selection — which DAC the graph binds.

Spec: Documents/specs/audio-output-selection-spec.md.

The appliance has never had a STATED audio output. It had a guess, re-derived on
every start by whichever heuristic was nearest to hand, and the guess was wrong
in a different way each time — most expensively when `grep -i "JACK"` matched a
DAC named "USB-C to 3.5mm Headphone Jack A" and Surge opened raw ALSA while
engine.state read ok.

Two rules govern everything here:

1. A device is named by its USB identity, never by a card index, a card id, or
   its product string. Enumeration is NOT reimplemented in Python — it comes
   from scripts/list-audio-outputs.sh, the same code the graph start uses.
2. A stored selection is a PREFERENCE, never a command. It applies when the
   device is present; otherwise the appliance falls through to Automatic and
   says so by name.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIST_OUTPUTS_SCRIPT = REPO_ROOT / "scripts" / "list-audio-outputs.sh"
SET_SURGE_AUDIO_SCRIPT = REPO_ROOT / "scripts" / "set-surge-audio.sh"
MPE_ENV_PATH = Path("/etc/mpe/mpe.env")

AUTO = "auto"
SILENT = "silent"

# Enumeration shells out; the graph may be busy. Generous, but bounded.
LIST_TIMEOUT_S = 10.0

# Same reasoning as surge_audio.AUDIO_SWITCH_TIMEOUT_S: an output change restarts
# the whole graph, and subprocess.run(timeout=) escalates to SIGKILL, which the
# script cannot trap. Recovery lives in the crash marker, not in this margin.
OUTPUT_SWITCH_TIMEOUT_S = 150.0
TERMINATE_GRACE_S = 10.0


@dataclass(frozen=True)
class OutputDevice:
    index: str
    card_id: str
    key: str
    speed: str
    product: str

    @property
    def selectable(self) -> bool:
        """A device with no USB identity can be bound automatically but never
        stored as a selection — there is nothing stable to store."""
        return bool(self.key)

    @property
    def speed_label(self) -> str:
        return speed_label(self.speed)


def speed_label(speed: str) -> str:
    """Mirrors mpe_output_speed_label. Shown on the row because it is the single
    strongest predictor of the smallest period the device can run, and is
    otherwise invisible: measured 2026-09-01, the Apple dongle and the FiiO KA1
    both enumerate full speed and neither starts a driver at 64, while the
    Scarlett 4i4 enumerates high speed and runs 64 and 32 clean."""
    return {
        "480": "high speed",
        "12": "full speed",
        "5000": "SuperSpeed",
        "10000": "SuperSpeed",
        "": "unknown speed",
    }.get((speed or "").strip(), f"{speed} Mbps")


def list_outputs() -> tuple[OutputDevice, ...]:
    """Present, selectable outputs — from the shell enumerator, not a copy."""
    if not LIST_OUTPUTS_SCRIPT.is_file():
        return ()
    try:
        result = subprocess.run(
            [str(LIST_OUTPUTS_SCRIPT)],
            capture_output=True, text=True, timeout=LIST_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    devices = []
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip():
            continue
        devices.append(OutputDevice(*(p.strip() for p in parts)))
    return tuple(devices)


def read_selection_from_env_file(path: Path = MPE_ENV_PATH) -> tuple[str, str]:
    """(key, label) as stored. Missing file or key means Automatic."""
    key, label = "", ""
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MPE_AUDIO_OUTPUT="):
                key = line.partition("=")[2].strip().strip("\"'")
            elif line.startswith("MPE_AUDIO_OUTPUT_LABEL="):
                label = line.partition("=")[2].strip().strip("\"'")
    return normalize_selection(key), label


def normalize_selection(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw or raw.lower() == AUTO:
        return AUTO
    if raw.lower() == SILENT:
        return SILENT
    return raw


def current_selection() -> tuple[str, str]:
    env_path = MPE_ENV_PATH
    raw = os.environ.get("MPE_ENV_FILE")
    if raw is not None:
        if not raw.strip():
            return (normalize_selection(os.environ.get("MPE_AUDIO_OUTPUT")),
                    os.environ.get("MPE_AUDIO_OUTPUT_LABEL", ""))
        env_path = Path(raw.strip())
    return read_selection_from_env_file(env_path)


@dataclass(frozen=True)
class MenuRow:
    key: str
    title: str
    subtitle: str
    selected: bool
    enabled: bool


def menu_rows(
    devices: tuple[OutputDevice, ...] | None = None,
    selection: str | None = None,
    label: str | None = None,
) -> tuple[MenuRow, ...]:
    """The menu: Automatic, Silent, and the devices that are actually here.

    Absent devices are NOT offered — Mitch, 2026-09-01: "why is a not-available
    device shown?" A row you cannot pick is not a choice, and offering one
    invites exactly the selection this feature exists to prevent.

    The one exception is not selectable: when the SAVED device is absent, it is
    shown inert and marked, because otherwise the stored preference is invisible
    and there is no way to tell whether one was ever set. It also explains the
    fall-through the user is currently hearing.
    """
    if devices is None:
        devices = list_outputs()
    if selection is None:
        selection, stored_label = current_selection()
        if label is None:
            label = stored_label
    selection = normalize_selection(selection)
    label = label or ""

    rows = [
        MenuRow(AUTO, "Automatic",
                "Pick the best device that is connected",
                selection == AUTO, True),
        MenuRow(SILENT, "Silent (no output)",
                "Bind the idle sink on purpose — nothing will be audible",
                selection == SILENT, True),
    ]

    present_keys = set()
    for device in devices:
        if not device.selectable:
            continue
        present_keys.add(device.key)
        rows.append(MenuRow(
            device.key,
            device.product,
            f"{device.speed_label} · card {device.card_id}",
            device.key == selection,
            True,
        ))

    if selection not in (AUTO, SILENT) and selection not in present_keys:
        rows.append(MenuRow(
            selection,
            label or selection,
            "saved — not connected",
            True,
            False,
        ))
    return tuple(rows)


def output_settings_label() -> str:
    selection, label = current_selection()
    if selection == AUTO:
        return "Audio device — Automatic"
    if selection == SILENT:
        return "Audio device — Silent"
    for device in list_outputs():
        if device.key == selection:
            return f"Audio device — {device.product}"
    return f"Audio device — {label or selection} (not connected)"


def apply_output(key: str, label: str = "") -> tuple[bool, str]:
    """Apply through set-surge-audio.sh — the flock, the crash marker and the
    reconcile all live there. `set-audio-profile.sh` was the second door into
    the same failure and had none of them; a third must not be opened."""
    key = normalize_selection(key)
    if not SET_SURGE_AUDIO_SCRIPT.is_file():
        return False, "set-surge-audio.sh missing"

    args = ["sudo", str(SET_SURGE_AUDIO_SCRIPT), "--output", key]
    if label:
        args += ["--output-label", label]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        return False, str(exc)[:60]

    try:
        stdout, stderr = proc.communicate(timeout=OUTPUT_SWITCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return False, f"Timed out ({int(OUTPUT_SWITCH_TIMEOUT_S)}s)"

    if proc.returncode != 0:
        detail = (stderr or stdout or "output change failed").strip()
        return False, detail.splitlines()[0][:60]

    os.environ["MPE_AUDIO_OUTPUT"] = key
    os.environ["MPE_AUDIO_OUTPUT_LABEL"] = label
    if key == AUTO:
        return True, "Audio device — Automatic"
    if key == SILENT:
        return True, "Audio device — Silent (nothing will be audible)"
    return True, f"Audio device — {label or key}"
