"""Surge ALSA buffer size and sample rate — persisted in /etc/mpe/mpe.env."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

MPE_ENV_PATH = Path("/etc/mpe/mpe.env")
SET_SURGE_AUDIO_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set-surge-audio.sh"

BUFFER_PRESETS: tuple[int, ...] = (32, 64, 128, 256, 512, 768, 1024, 2048)
SAMPLE_RATE_PRESETS: tuple[int, ...] = (44100, 48000)

# Must stay in sync with the fallback in scripts/start-surge-cli.sh.
# 768 drops voices under heavy MPE polyphony on Pi 4; 512 choked outright.
DEFAULT_BUFFER = 1024
DEFAULT_SAMPLE_RATE = 48000

AUDIO_SWITCH_TIMEOUT_S = 45.0


def _read_env_int(key: str, default: int, *, valid: frozenset[int]) -> int:
    from_file = read_int_from_env_file(key, MPE_ENV_PATH)
    if from_file is not None and from_file in valid:
        return from_file
    raw = os.environ.get(key)
    if raw is not None:
        try:
            value = int(str(raw).strip())
        except ValueError:
            return default
        if value in valid:
            return value
    return default


def read_int_from_env_file(key: str, path: Path = MPE_ENV_PATH) -> int | None:
    if not path.is_file():
        return None
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(\d+)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return int(match.group(1))
    return None


def current_buffer_size() -> int:
    return _read_env_int(
        "MPE_SURGE_BUFFER_SIZE",
        DEFAULT_BUFFER,
        valid=frozenset(BUFFER_PRESETS),
    )


def current_sample_rate() -> int:
    return _read_env_int(
        "MPE_SURGE_SAMPLE_RATE",
        DEFAULT_SAMPLE_RATE,
        valid=frozenset(SAMPLE_RATE_PRESETS),
    )


def buffer_latency_ms(buffer: int | None = None, sample_rate: int | None = None) -> float:
    buf = buffer if buffer is not None else current_buffer_size()
    rate = sample_rate if sample_rate is not None else current_sample_rate()
    if rate <= 0:
        return 0.0
    return buf * 1000.0 / rate


def buffer_option_label(buffer: int, sample_rate: int | None = None) -> str:
    ms = buffer_latency_ms(buffer, sample_rate)
    return f"{buffer} · {ms:.0f} ms"


def sample_rate_option_label(sample_rate: int) -> str:
    if sample_rate == 44100:
        return "44.1 kHz"
    return f"{sample_rate // 1000} kHz"


def buffer_settings_label() -> str:
    return f"Audio buffer — {buffer_option_label(current_buffer_size())}"


def sample_rate_settings_label() -> str:
    return f"Sample rate — {sample_rate_option_label(current_sample_rate())}"


def next_buffer_preset(current: int | None = None) -> int:
    value = current if current is not None else current_buffer_size()
    if value not in BUFFER_PRESETS:
        return DEFAULT_BUFFER
    index = BUFFER_PRESETS.index(value)
    return BUFFER_PRESETS[(index + 1) % len(BUFFER_PRESETS)]


def next_sample_rate(current: int | None = None) -> int:
    value = current if current is not None else current_sample_rate()
    if value not in SAMPLE_RATE_PRESETS:
        return DEFAULT_SAMPLE_RATE
    index = SAMPLE_RATE_PRESETS.index(value)
    return SAMPLE_RATE_PRESETS[(index + 1) % len(SAMPLE_RATE_PRESETS)]


def apply_buffer(buffer: int) -> tuple[bool, str]:
    if buffer not in BUFFER_PRESETS:
        return False, f"Invalid buffer: {buffer}"
    return _run_set_script(["--buffer", str(buffer)], success=f"Buffer {buffer}")


def apply_sample_rate(sample_rate: int) -> tuple[bool, str]:
    if sample_rate not in SAMPLE_RATE_PRESETS:
        return False, f"Invalid rate: {sample_rate}"
    return _run_set_script(
        ["--sample-rate", str(sample_rate)],
        success=f"Sample rate {sample_rate // 1000} kHz",
    )


def _run_set_script(args: list[str], *, success: str) -> tuple[bool, str]:
    if not SET_SURGE_AUDIO_SCRIPT.is_file():
        return False, "set-surge-audio.sh missing"

    try:
        result = subprocess.run(
            ["sudo", str(SET_SURGE_AUDIO_SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=AUDIO_SWITCH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"Timed out ({int(AUDIO_SWITCH_TIMEOUT_S)}s)"
    except OSError as exc:
        return False, str(exc)[:60]

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "apply failed").strip()
        return False, detail.splitlines()[0][:60]

    if "--buffer" in args:
        os.environ["MPE_SURGE_BUFFER_SIZE"] = args[args.index("--buffer") + 1]
    if "--sample-rate" in args:
        os.environ["MPE_SURGE_SAMPLE_RATE"] = args[args.index("--sample-rate") + 1]

    ms = buffer_latency_ms()
    if "--buffer" in args and "--sample-rate" not in args:
        buf = int(args[args.index("--buffer") + 1])
        return True, f"{success} (~{buffer_latency_ms(buf):.0f} ms)"
    if "--sample-rate" in args:
        return True, f"{success} — restart host capture at new rate if USB"
    return True, success
