"""Looper-grid MIDI timing — output offset and quantize for pedal ↔ Pi play path."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from patch_browser.midi_clock import PPQN, normalize_midi_bytes
from patch_browser.surge_audio import DEFAULT_BUFFER, DEFAULT_SAMPLE_RATE

def _env_int(key: str, default: int | None) -> int | None:
    """Int from env, or *default* when unset/garbage — env files carry stale junk."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


MIDI_CLOCK = 0xF8
MIDI_START = 0xFA
MIDI_CONTINUE = 0xFB
MIDI_STOP = 0xFC

QUANTIZE_CHOICES: dict[str, int] = {
    "off": 0,
    "beat": PPQN,
    "8th": PPQN // 2,
    "16th": PPQN // 4,
    "32nd": PPQN // 8,
}


def triplet_enabled() -> bool:
    return os.environ.get("MPE_MIDI_QUANTIZE_TRIPLET", "0").strip().lower() in ("1", "true", "yes", "on")


def parse_quantize_grid_ticks(value: str | None, *, triplet: bool | None = None) -> int:
    if not value or not str(value).strip():
        return 0
    key = str(value).strip().lower()
    if key in ("0", "false", "no", "none"):
        return 0
    # Legacy env: MPE_MIDI_QUANTIZE=triplet → 8th-note triplet grid.
    if key == "triplet":
        key = "8th"
        if triplet is None:
            triplet = True
    base = QUANTIZE_CHOICES.get(key, QUANTIZE_CHOICES["16th"])
    if base <= 0:
        return 0
    use_triplet = triplet_enabled() if triplet is None else triplet
    if use_triplet:
        return max(1, base * 2 // 3)
    return base


def resolve_quantize_grid_ticks() -> int:
    """Grid ticks from MPE_MIDI_QUANTIZE + MPE_MIDI_QUANTIZE_TRIPLET."""
    return parse_quantize_grid_ticks(os.environ.get("MPE_MIDI_QUANTIZE"))


def _running_graph_params() -> tuple[int, int, int] | None:
    """(period, periods, rate) the JACK server is ACTUALLY running, or None.

    /run/mpe/jack.state is written by start-jackd.sh once the driver has proved
    it can accept clients, so it records what the server got -- not what was
    asked for. Those differ: the fallback ladder climbs when a DAC cannot
    sustain the configured period, and a 64 -> 256 climb leaves MPE_JACK_BUFFER
    saying 64 while the server runs 256. Compensating for a period the server is
    not running is a 4x error in the direction that fires MIDI late.
    """
    try:
        from patch_browser.audio_engine import read_jack_state

        state = read_jack_state()
    except Exception:
        return None
    try:
        period = int(str(state.get("period", "")).strip())
        n_periods = int(str(state.get("periods", "")).strip())
        rate = int(str(state.get("rate", "")).strip())
    except (TypeError, ValueError):
        return None
    if period <= 0 or n_periods <= 0 or rate <= 0:
        return None
    return period, n_periods, rate


def buffer_latency_ms(
    buffer: int | None = None,
    sample_rate: int | None = None,
    periods: int | None = None,
) -> float:
    """Output latency of the audio path in ms — the JACK period × periods.

    Under the JACK graph server the period belongs to the server and the real
    output latency is ``period × periods``, not one period. Reading
    MPE_SURGE_BUFFER_SIZE and skipping the periods term under-reported latency by 3×
    at the shipped default, which fires MIDI too late against the looper grid.

    Source of truth is jack.state — what the server IS running — falling back to
    the env only when the graph has not published yet. MPE_SURGE_BUFFER_SIZE is
    the legacy Surge ALSA key and is NOT the graph period; it is consulted last
    and only because an appliance predating the JACK keys would otherwise
    compute zero.
    """
    running = _running_graph_params() if (buffer is None and periods is None
                                          and sample_rate is None) else None
    if running is not None:
        buf, n_periods, rate = running
    else:
        if buffer is not None:
            buf = buffer
        else:
            buf = _env_int("MPE_JACK_BUFFER", None) or _env_int(
                "MPE_SURGE_BUFFER_SIZE", DEFAULT_BUFFER
            )
        n_periods = periods if periods is not None else _env_int("MPE_JACK_PERIODS", 3)
        rate = sample_rate if sample_rate is not None else _env_int(
            "MPE_SURGE_SAMPLE_RATE", DEFAULT_SAMPLE_RATE
        )
    if rate <= 0 or n_periods <= 0:
        return 0.0
    return 1000.0 * buf * n_periods / rate


# Surge's own MIDI-in -> audio-out leg, which JACK cannot see and the offset
# omitted entirely until 2026-09-02.
#
# MEASURED, n=30 per period (docs/measurements/midi-audio-latency-phase1-2026-09-02.md):
#
#     period  96 -> 159 frames      period 192 -> 249 frames
#
# Both distributions tight and unimodal. The slope between them is 0.94 and the
# mechanism -- Surge picking MIDI up on the NEXT process callback -- predicts
# exactly 1.0, so the model is one period plus a fixed dispatch cost. That fits
# both points to within 3 frames (0.06 ms):
#
#     96 + 60 = 156 (measured 159)      192 + 60 = 252 (measured 249)
#
# A period-256 cell was run and DISCARDED: it returned a strict low/high
# alternation every other trial, which is a harness artifact, not a property of
# Surge. So this law is anchored on two periods and should be re-measured before
# it is trusted far outside 96-192.
SURGE_MIDI_LEG_CONST_FRAMES = 60

OUTPUT_LATENCY_CONF = Path(__file__).resolve().parents[1] / "config" / "output-latency.conf"


def surge_midi_leg_frames(period: int) -> int:
    """Frames from a MIDI byte reaching Surge to its audio leaving Surge's port."""
    if period <= 0:
        return 0
    return period + SURGE_MIDI_LEG_CONST_FRAMES


def _running_card_key() -> str | None:
    """usb:VID:PID of the card the graph is ACTUALLY bound to, or None.

    Resolved from jack.state's device rather than the configured output, because
    an absent selection legitimately falls through to another tier -- and
    compensating for a DAC that is not the one making sound is exactly the class
    of error this whole exercise exists to remove.
    """
    try:
        from patch_browser.audio_engine import read_jack_state

        device = str(read_jack_state().get("device", "")).strip()
    except Exception:
        return None
    if not device.startswith("hw:"):
        return None
    index = device[3:].split(",", 1)[0].strip()
    if not index.isdigit():
        return None
    try:
        usbid = Path(f"/proc/asound/card{index}/usbid").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return f"usb:{usbid}" if usbid else None


def _load_output_latency_table() -> dict[str, int]:
    table: dict[str, int] = {}
    try:
        text = OUTPUT_LATENCY_CONF.read_text(encoding="utf-8")
    except OSError:
        return table
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            table[parts[0]] = int(parts[1])
    return table


def output_hardware_latency_frames() -> int:
    """Measured DAC latency beyond JACK's declaration, or 0 when unmeasured.

    Zero is the honest answer for a device nobody has put on a loopback -- but it
    is a KNOWN GAP, not a measurement, and must never be filled by guessing from
    a similar-looking device. See config/output-latency.conf.
    """
    key = _running_card_key()
    if not key:
        return 0
    return _load_output_latency_table().get(key, 0)


def total_output_latency_ms() -> float:
    """Every measured leg from a MIDI byte to sound in the air, in ms.

    Three terms, only one of which the offset used before 2026-09-02:

        Surge MIDI -> its output port   measured   (period + 60 frames)
        its port   -> the converter     declared   (period x periods)
        the DAC itself                  measured   (config/output-latency.conf)

    The old model was the middle term alone -- 192 frames at period 96 x 2,
    against ~415 actual. It under-compensated by more than half the real figure,
    in the direction that fires MIDI LATE against the looper grid.
    """
    running = _running_graph_params()
    if running is not None:
        period, n_periods, rate = running
    else:
        period = _env_int("MPE_JACK_BUFFER", None) or _env_int(
            "MPE_SURGE_BUFFER_SIZE", DEFAULT_BUFFER
        )
        n_periods = _env_int("MPE_JACK_PERIODS", 3)
        rate = _env_int("MPE_SURGE_SAMPLE_RATE", DEFAULT_SAMPLE_RATE)
    if not period or not n_periods or not rate or rate <= 0:
        return 0.0
    frames = (
        surge_midi_leg_frames(period)
        + period * n_periods
        + output_hardware_latency_frames()
    )
    return 1000.0 * frames / rate


def resolve_output_offset_ms() -> float:
    """Negative ms = fire MIDI earlier so Surge audio aligns with looper grid."""
    raw = os.environ.get("MPE_MIDI_OUTPUT_OFFSET_MS", "").strip()
    if raw:
        return float(raw)
    if os.environ.get("MPE_MIDI_OUTPUT_OFFSET_AUTO", "1").strip().lower() in ("1", "true", "yes"):
        return -total_output_latency_ms()
    return 0.0


def clock_through_enabled() -> bool:
    return os.environ.get("MPE_MIDI_CLOCK_THROUGH", "1").strip().lower() in ("1", "true", "yes")


def is_realtime_clock_byte(status: int) -> bool:
    return status in (MIDI_CLOCK, MIDI_START, MIDI_CONTINUE, MIDI_STOP)


def is_note_on(message: list[int]) -> bool:
    if len(message) < 3 or message[0] < 0x80:
        return False
    return (message[0] & 0xF0) == 0x90 and message[2] > 0


def is_note_off(message: list[int]) -> bool:
    if len(message) < 3 or message[0] < 0x80:
        return False
    hi = message[0] & 0xF0
    return hi == 0x80 or (hi == 0x90 and message[2] == 0)


def tick_interval_seconds_from_snap(clock_snap: dict[str, Any]) -> float | None:
    bpm = clock_snap.get("bpm_raw") or clock_snap.get("bpm")
    if bpm is None:
        return None
    try:
        bpm_f = float(bpm)
    except (TypeError, ValueError):
        return None
    if bpm_f <= 0:
        return None
    return 60.0 / bpm_f / PPQN


def next_grid_monotonic(
    now: float,
    clock_snap: dict[str, Any],
    grid_ticks: int,
) -> float:
    """Monotonic time of the next grid line at or after ``now``."""
    if grid_ticks <= 0:
        return now
    interval = tick_interval_seconds_from_snap(clock_snap)
    if interval is None:
        return now
    transport_ticks = int(clock_snap.get("transport_ticks") or 0)
    pos = transport_ticks % grid_ticks
    if pos == 0:
        return now
    ticks_wait = grid_ticks - pos
    return now + ticks_wait * interval


def plan_fire_at(
    now: float,
    clock_snap: dict[str, Any],
    *,
    quantize: bool,
    grid_ticks: int,
    offset_ms: float,
) -> float:
    base = now
    if quantize and grid_ticks > 0 and clock_snap.get("synced") and clock_snap.get("running"):
        base = next_grid_monotonic(now, clock_snap, grid_ticks)
    return base + offset_ms / 1000.0


def should_schedule(message: list[int], *, quantize_note_on: bool) -> bool:
    if is_note_on(message):
        return quantize_note_on
    if is_note_off(message):
        return True
    return False


def prepare_incoming(message) -> list[int]:
    return normalize_midi_bytes(message)
