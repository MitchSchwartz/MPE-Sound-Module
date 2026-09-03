"""Looper MIDI sync settings — persisted in /etc/mpe/mpe.env."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from patch_browser.midi_sync import (
    QUANTIZE_CHOICES,
    parse_quantize_grid_ticks,
)
from patch_browser.surge_audio import MPE_ENV_PATH

SET_MIDI_SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set-midi-sync.sh"

QUANTIZE_OPTIONS: tuple[str, ...] = tuple(QUANTIZE_CHOICES.keys())
DEFAULT_QUANTIZE = "off"
DEFAULT_OFFSET_AUTO = True
DEFAULT_TRIPLET = False

APPLY_TIMEOUT_S = 20.0


def read_str_from_env_file(key: str, path: Path = MPE_ENV_PATH) -> str | None:
    if not path.is_file():
        return None
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip().strip('"').strip("'")
    return None


def _env_bool(key: str, default: bool) -> bool:
    raw = read_str_from_env_file(key, MPE_ENV_PATH)
    if raw is None:
        raw = os.environ.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _normalize_quantize_key(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return DEFAULT_QUANTIZE
    key = str(raw).strip().lower()
    if key == "triplet":
        return "8th"
    if key in QUANTIZE_CHOICES:
        return key
    return DEFAULT_QUANTIZE


def current_quantize() -> str:
    raw = read_str_from_env_file("MPE_MIDI_QUANTIZE", MPE_ENV_PATH)
    if raw is None:
        raw = os.environ.get("MPE_MIDI_QUANTIZE")
    return _normalize_quantize_key(raw)


def current_triplet() -> bool:
    raw = read_str_from_env_file("MPE_MIDI_QUANTIZE", MPE_ENV_PATH)
    if raw is not None and str(raw).strip().lower() == "triplet":
        return True
    return _env_bool("MPE_MIDI_QUANTIZE_TRIPLET", DEFAULT_TRIPLET)


def current_offset_auto() -> bool:
    return _env_bool("MPE_MIDI_OUTPUT_OFFSET_AUTO", DEFAULT_OFFSET_AUTO)


def quantize_subdivision_label(value: str) -> str:
    labels = {
        "off": "Off",
        "beat": "4th note",
        "8th": "8th note",
        "16th": "16th note",
        "32nd": "32nd note",
    }
    return labels.get(value, value)


def quantize_option_label(value: str) -> str:
    label = quantize_subdivision_label(value)
    if value != "off" and current_triplet():
        return f"{label} (triplet)"
    return label


def offset_ms_value() -> str:
    """What the runtime will actually apply — computed by the runtime's own
    function, not re-derived here.

    MEASURED 2026-09-01: this displayed "−43 ms" while the appliance applied
    −4 ms. It called buffer_latency_ms(current_buffer_size(), ...), and
    current_buffer_size() is MPE_SURGE_BUFFER_SIZE -- the LEGACY Surge ALSA key,
    1024 on the appliance -- whose own docstring in surge_audio.py says it is
    "not the playing JACK period". The runtime used MPE_JACK_BUFFER (96). A 10x
    disagreement between the number shown and the number used, on the one screen
    you would look at to decide whether the offset was sane.

    Two computations of one quantity is the bug. There is now one, and this is a
    formatter for it.
    """
    if current_offset_auto():
        from patch_browser.midi_sync import resolve_output_offset_ms

        return f"{resolve_output_offset_ms():+.0f} ms"
    raw = read_str_from_env_file("MPE_MIDI_OUTPUT_OFFSET_MS", MPE_ENV_PATH)
    if raw:
        try:
            return f"{float(raw):+.0f} ms"
        except ValueError:
            pass
    return "manual"


def offset_toggle_label() -> str:
    return f"Auto offset ({offset_ms_value()})"


def offset_summary() -> str:
    return offset_toggle_label()


def settings_summary() -> str:
    return " · ".join(settings_summary_lines())


def settings_summary_lines() -> list[str]:
    from patch_browser.ui_text import settings_detail_lines

    return settings_detail_lines(
        f"Quantize: {quantize_option_label(current_quantize())}",
        offset_summary(),
    )


def settings_row_label() -> str:
    return f"Looper sync — {settings_summary()}"


def _run_set_script(args: list[str], *, success: str) -> tuple[bool, str]:
    if not SET_MIDI_SYNC_SCRIPT.is_file():
        return False, "set-midi-sync.sh missing"

    try:
        result = subprocess.run(
            ["sudo", str(SET_MIDI_SYNC_SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=APPLY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"Timed out ({int(APPLY_TIMEOUT_S)}s)"
    except OSError as exc:
        return False, str(exc)[:60]

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "apply failed").strip()
        return False, detail.splitlines()[0][:60]

    if "--quantize" in args:
        os.environ["MPE_MIDI_QUANTIZE"] = args[args.index("--quantize") + 1]
    if "--triplet" in args:
        os.environ["MPE_MIDI_QUANTIZE_TRIPLET"] = args[args.index("--triplet") + 1]
    if "--offset-auto" in args:
        os.environ["MPE_MIDI_OUTPUT_OFFSET_AUTO"] = args[args.index("--offset-auto") + 1]

    return True, success


def apply_quantize(value: str) -> tuple[bool, str]:
    key = _normalize_quantize_key(value)
    if key not in QUANTIZE_CHOICES:
        return False, f"Invalid quantize: {value}"
    label = quantize_option_label(key)
    ticks = parse_quantize_grid_ticks(key, triplet=current_triplet())
    detail = f" ({ticks} ticks)" if ticks else ""
    return _run_set_script(["--quantize", key], success=f"Quantize {label}{detail}")


def apply_triplet(enabled: bool) -> tuple[bool, str]:
    flag = "1" if enabled else "0"
    label = "Triplet on" if enabled else "Triplet off"
    return _run_set_script(["--triplet", flag], success=label)


def apply_offset_auto(enabled: bool) -> tuple[bool, str]:
    flag = "1" if enabled else "0"
    label = "Auto buffer offset on" if enabled else "Auto buffer offset off"
    return _run_set_script(["--offset-auto", flag], success=label)
