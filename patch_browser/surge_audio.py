"""Surge audio settings — JACK graph buffer (display) and legacy Surge keys."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

MPE_ENV_PATH = Path("/etc/mpe/mpe.env")
SET_SURGE_AUDIO_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set-surge-audio.sh"

# Legacy Surge ALSA sizes — still valid in mpe.env for MIDI offset / calibration.
BUFFER_PRESETS: tuple[int, ...] = (32, 64, 128, 256, 512, 768, 1024, 2048)
# JACK server period sizes the touch UI and jackd accept (see mpe-cli jack buffer).
JACK_PERIOD_PRESETS: tuple[int, ...] = (32, 64, 128, 256, 512, 1024)
JACK_PERIODS_PRESETS: tuple[int, ...] = (2, 3, 4)
SAMPLE_RATE_PRESETS: tuple[int, ...] = (44100, 48000)

# Legacy Surge key — still read by calibration and MIDI offset auto-derivation.
DEFAULT_BUFFER = 1024
DEFAULT_JACK_PERIOD = 256
DEFAULT_JACK_PERIODS = 3
DEFAULT_SAMPLE_RATE = 48000

# `subprocess.run(timeout=...)` KILLS the child, and set-surge-audio.sh restarts
# the whole JACK graph — slowest exactly when the graph is already unhappy, which
# is when a buffer change is most likely to be attempted. At 45 s that kill
# landed between the env write and its validation and left an untested buffer in
# /etc/mpe/mpe.env; the appliance booted into it, dead (2026-09-01).
#
# The script does NOT survive that kill and cannot be made to: `subprocess.run`
# escalates to Popen.kill() == SIGKILL, which is untrappable, and the child here
# is `sudo`, so depending on the build the signal either kills the script outright
# or kills sudo and orphans it. Recovery therefore does not live in the script's
# lifetime at all — set-surge-audio.sh writes /etc/mpe/mpe.env.pending before
# mutating anything and mpe-jackd's ExecStartPre reconciles it on the next graph
# start. This margin is a courtesy so ordinary slow changes are not interrupted.
AUDIO_SWITCH_TIMEOUT_S = 150.0

# How long a TERM gets to run the script's own rollback before SIGKILL. The
# rollback is three sed-and-install passes over a small file — well under a
# second — so this is generous.
TERMINATE_GRACE_S = 10.0


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
    """Legacy Surge buffer key — not the playing JACK period (see current_jack_period)."""
    return _read_env_int(
        "MPE_SURGE_BUFFER_SIZE",
        DEFAULT_BUFFER,
        valid=frozenset(BUFFER_PRESETS),
    )


def current_jack_period() -> int:
    return _read_env_int(
        "MPE_JACK_BUFFER",
        DEFAULT_JACK_PERIOD,
        valid=frozenset(JACK_PERIOD_PRESETS),
    )


def current_jack_periods() -> int:
    return _read_env_int(
        "MPE_JACK_PERIODS",
        DEFAULT_JACK_PERIODS,
        valid=frozenset(JACK_PERIODS_PRESETS),
    )


def current_sample_rate() -> int:
    return _read_env_int(
        "MPE_SURGE_SAMPLE_RATE",
        DEFAULT_SAMPLE_RATE,
        valid=frozenset(SAMPLE_RATE_PRESETS),
    )


def buffer_latency_ms(buffer: int | None = None, sample_rate: int | None = None) -> float:
    """Single-period latency for a buffer size (legacy helper)."""
    buf = buffer if buffer is not None else current_buffer_size()
    rate = sample_rate if sample_rate is not None else current_sample_rate()
    if rate <= 0:
        return 0.0
    return buf * 1000.0 / rate


def graph_latency_ms(
    period: int | None = None,
    periods: int | None = None,
    sample_rate: int | None = None,
) -> float:
    """End-to-end JACK server latency (period × periods)."""
    jack_period = period if period is not None else current_jack_period()
    jack_periods = periods if periods is not None else current_jack_periods()
    rate = sample_rate if sample_rate is not None else current_sample_rate()
    if rate <= 0:
        return 0.0
    return jack_period * jack_periods * 1000.0 / rate


def buffer_option_label(buffer: int, sample_rate: int | None = None) -> str:
    ms = buffer_latency_ms(buffer, sample_rate)
    return f"{buffer} · {ms:.0f} ms"


def graph_buffer_option_label(
    period: int | None = None,
    periods: int | None = None,
    sample_rate: int | None = None,
) -> str:
    jack_period = period if period is not None else current_jack_period()
    jack_periods = periods if periods is not None else current_jack_periods()
    ms = graph_latency_ms(jack_period, jack_periods, sample_rate)
    return f"{jack_period} × {jack_periods} · {ms:.0f} ms"


def sample_rate_option_label(sample_rate: int) -> str:
    if sample_rate == 44100:
        return "44.1 kHz"
    return f"{sample_rate // 1000} kHz"


def buffer_settings_label() -> str:
    return f"Audio buffer — {graph_buffer_option_label()}"


def sample_rate_settings_label() -> str:
    return f"Sample rate — {sample_rate_option_label(current_sample_rate())}"


def next_buffer_preset(current: int | None = None) -> int:
    value = current if current is not None else current_jack_period()
    if value not in JACK_PERIOD_PRESETS:
        return DEFAULT_JACK_PERIOD
    index = JACK_PERIOD_PRESETS.index(value)
    return JACK_PERIOD_PRESETS[(index + 1) % len(JACK_PERIOD_PRESETS)]


def next_sample_rate(current: int | None = None) -> int:
    value = current if current is not None else current_sample_rate()
    if value not in SAMPLE_RATE_PRESETS:
        return DEFAULT_SAMPLE_RATE
    index = SAMPLE_RATE_PRESETS.index(value)
    return SAMPLE_RATE_PRESETS[(index + 1) % len(SAMPLE_RATE_PRESETS)]


def apply_buffer(buffer: int) -> tuple[bool, str]:
    if buffer not in JACK_PERIOD_PRESETS:
        return False, f"Invalid JACK period: {buffer}"
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

    # Deliberately not subprocess.run(timeout=): that escalates straight to
    # SIGKILL, which the script cannot trap and which orphans it when sudo forks
    # a monitor. Ask politely first — a TERM lets the in-process rollback run and
    # tidy up immediately, which is much faster than waiting for the next graph
    # start to reconcile the marker. SIGKILL stays as the last resort, and the
    # marker covers whatever it leaves behind.
    try:
        proc = subprocess.Popen(
            ["sudo", str(SET_SURGE_AUDIO_SCRIPT), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return False, str(exc)[:60]

    try:
        stdout, stderr = proc.communicate(timeout=AUDIO_SWITCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return False, f"Timed out ({int(AUDIO_SWITCH_TIMEOUT_S)}s)"

    result = subprocess.CompletedProcess(
        proc.args, proc.returncode, stdout=stdout, stderr=stderr
    )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "apply failed").strip()
        return False, detail.splitlines()[0][:60]

    if "--buffer" in args:
        os.environ["MPE_SURGE_BUFFER_SIZE"] = args[args.index("--buffer") + 1]
    if "--sample-rate" in args:
        os.environ["MPE_SURGE_SAMPLE_RATE"] = args[args.index("--sample-rate") + 1]

    if "--buffer" in args and "--sample-rate" not in args:
        buf = int(args[args.index("--buffer") + 1])
        ms = graph_latency_ms(buf)
        return True, f"{success} (~{ms:.0f} ms graph latency)"
    if "--sample-rate" in args:
        return True, f"{success} — restart host capture at new rate if USB"
    return True, success
