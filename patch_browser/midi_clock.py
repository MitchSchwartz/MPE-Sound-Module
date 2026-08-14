"""MIDI clock helpers — timing math, port discovery, and looper sync state."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

# Standard MIDI clock: 24 pulses per quarter note.
PPQN = 24

MIDI_CLOCK = 0xF8
MIDI_START = 0xFA
MIDI_CONTINUE = 0xFB
MIDI_STOP = 0xFC

CLOCK_STATE_FILE = Path.home() / ".mpe_midi_clock_state.json"

# Do not route clock through Surge or internal ALSA plumbing.
SKIP_CLOCK_PORT_SUBSTRINGS = (
    "surge xt",
    "midi through",
    "through port",
    "rtmidi output",
    "rtmidi input",
)

# Prefer looper pedals when listening for incoming clock.
LOOPER_INPUT_PORT_SUBSTRINGS = (
    "rc-5",
    "rc5",
    "rc-500",
    "rc500",
    "rc-600",
    "boss",
    "loop",
)

# Never treat MPE controllers or control surfaces as clock sources.
SKIP_CLOCK_INPUT_PORT_SUBSTRINGS = SKIP_CLOCK_PORT_SUBSTRINGS + (
    "lumi",
    "seaboard",
    "roli",
    "apc",
    "akai",
    "launchpad",
    "control",
)


def tick_interval_seconds(bpm: float, *, ppqn: int = PPQN) -> float:
    """Seconds between MIDI clock ticks at the given tempo."""
    if bpm <= 0:
        raise ValueError(f"bpm must be positive, got {bpm!r}")
    if ppqn <= 0:
        raise ValueError(f"ppqn must be positive, got {ppqn!r}")
    return 60.0 / bpm / ppqn


def bpm_from_beat_duration(beat_duration_s: float) -> float | None:
    """Derive BPM from one quarter-note duration (seconds)."""
    if beat_duration_s <= 0:
        return None
    bpm = 60.0 / beat_duration_s
    if bpm < 20.0 or bpm > 400.0:
        return None
    return bpm


def stabilize_display_bpm(
    bpm: float | None,
    last_display: int | None,
    *,
    band: float = 0.6,
) -> int | None:
    """Round BPM for display with hysteresis so 109.4/110.6 don't flip the UI."""
    if bpm is None:
        return None
    candidate = round(bpm)
    if last_display is None:
        return candidate
    if candidate == last_display:
        return last_display
    if candidate > last_display and bpm < last_display + band:
        return last_display
    if candidate < last_display and bpm > last_display - band:
        return last_display
    return candidate


def bpm_from_tick_interval(interval_s: float, *, ppqn: int = PPQN) -> float | None:
    """Derive BPM from seconds between consecutive MIDI clock ticks."""
    if interval_s <= 0:
        return None
    return bpm_from_beat_duration(interval_s * ppqn)


def should_skip_clock_port(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in SKIP_CLOCK_PORT_SUBSTRINGS)


def should_skip_clock_input_port(name: str) -> bool:
    lower = name.lower()
    return any(token in lower for token in SKIP_CLOCK_INPUT_PORT_SUBSTRINGS)


def find_clock_output_port_index(
    port_names: list[str],
    *,
    prefer_substring: str | None = None,
) -> int | None:
    """Pick an RtMidi OUT port for external clock (Pi as master)."""
    if prefer_substring:
        needle = prefer_substring.strip().lower()
        if needle:
            for index, name in enumerate(port_names):
                if needle in name.lower() and not should_skip_clock_port(name):
                    return index

    for index, name in enumerate(port_names):
        if not should_skip_clock_port(name):
            return index
    return None


def find_clock_input_port_index(
    port_names: list[str],
    *,
    prefer_substring: str | None = None,
) -> int | None:
    """Pick an RtMidi IN port for looper clock (RC-5 USB, etc.)."""
    if prefer_substring:
        needle = prefer_substring.strip().lower()
        if needle:
            for index, name in enumerate(port_names):
                if needle in name.lower() and not should_skip_clock_input_port(name):
                    return index

    for index, name in enumerate(port_names):
        lower = name.lower()
        if should_skip_clock_input_port(name):
            continue
        if any(token in lower for token in LOOPER_INPUT_PORT_SUBSTRINGS):
            return index

    return None


def normalize_midi_bytes(message) -> list[int]:
    """Coerce RtMidi callback payloads to a flat list of MIDI bytes."""
    if message is None:
        return []
    if isinstance(message, (bytes, bytearray)):
        return [int(b) & 0xFF for b in message]
    if isinstance(message, int):
        return [message & 0xFF]
    if not isinstance(message, (list, tuple)):
        return []
    if (
        len(message) == 2
        and not isinstance(message[0], int)
        and isinstance(message[0], (list, tuple, bytes, bytearray))
    ):
        return normalize_midi_bytes(message[0])
    out: list[int] = []
    for item in message:
        if isinstance(item, int):
            out.append(item & 0xFF)
        elif isinstance(item, (list, tuple, bytes, bytearray)):
            out.extend(normalize_midi_bytes(item))
    return out


class MidiClockTracker:
    """Track transport + BPM from incoming MIDI clock messages."""

    def __init__(self, *, stale_after_s: float = 3.0, ema_alpha: float = 0.18) -> None:
        self.stale_after_s = stale_after_s
        self.ema_alpha = ema_alpha
        self.running = False
        self.bpm: float | None = None
        self._display_bpm: int | None = None
        self._last_tick_at: float | None = None
        self._beat_start_at: float | None = None
        self._tick_count = 0
        self.transport_ticks = 0
        self.port_name: str | None = None

    def set_port_name(self, name: str | None) -> None:
        self.port_name = name

    def _reset_beat_tracking(self) -> None:
        self._beat_start_at = None
        self._tick_count = 0

    def _note_bpm_sample(self, beat_duration_s: float) -> None:
        instant = bpm_from_beat_duration(beat_duration_s)
        if instant is None:
            return
        if self.bpm is None:
            self.bpm = instant
        else:
            self.bpm = self.ema_alpha * instant + (1.0 - self.ema_alpha) * self.bpm
        self._display_bpm = stabilize_display_bpm(self.bpm, self._display_bpm)

    def on_message(self, message, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        data = normalize_midi_bytes(message)
        if not data:
            return
        status = data[0]
        if status == MIDI_CLOCK:
            if self.running:
                self.transport_ticks += 1
            if self._beat_start_at is None:
                self._beat_start_at = now
            self._tick_count += 1
            if self._tick_count >= PPQN:
                beat_duration = now - self._beat_start_at
                if 0.05 < beat_duration < 3.0:
                    self._note_bpm_sample(beat_duration)
                self._reset_beat_tracking()
                self._beat_start_at = now
                self._tick_count = 0
            self._last_tick_at = now
            if not self.running:
                self.running = True
            return
        if status == MIDI_START:
            self.running = True
            self._last_tick_at = None
            self.bpm = None
            self._display_bpm = None
            self.transport_ticks = 0
            self._reset_beat_tracking()
            return
        if status == MIDI_CONTINUE:
            self.running = True
            return
        if status == MIDI_STOP:
            self.running = False
            self.transport_ticks = 0

    def snapshot(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        synced = (
            self._last_tick_at is not None
            and (now - self._last_tick_at) <= self.stale_after_s
        )
        display = self._display_bpm if synced else None
        ticks_in_beat = self.transport_ticks % PPQN if self.running else 0
        return {
            "connected": self.port_name is not None,
            "synced": synced,
            "running": self.running and synced,
            "bpm": display,
            "bpm_raw": self.bpm,
            "port": self.port_name,
            "transport_ticks": self.transport_ticks,
            "ticks_in_beat": ticks_in_beat,
            "updated_at": now,
        }


def write_clock_state(payload: dict, path: Path | None = None) -> None:
    target = path or CLOCK_STATE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".mpe_clock_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def read_clock_state(
    path: Path | None = None,
    *,
    stale_after_s: float = 5.0,
    now: float | None = None,
) -> dict:
    target = path or CLOCK_STATE_FILE
    now = time.monotonic() if now is None else now
    empty = {
        "connected": False,
        "synced": False,
        "running": False,
        "bpm": None,
        "port": None,
        "daemon_online": False,
    }
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return empty

    updated = float(data.get("updated_at", 0.0))
    daemon_online = (now - updated) <= stale_after_s
    synced = bool(data.get("synced")) and daemon_online
    return {
        "connected": bool(data.get("connected")) and daemon_online,
        "synced": synced,
        "running": bool(data.get("running")) and synced,
        "bpm": data.get("bpm") if synced else None,
        "bpm_raw": data.get("bpm_raw") if synced else None,
        "port": data.get("port"),
        "transport_ticks": int(data.get("transport_ticks") or 0) if synced else 0,
        "ticks_in_beat": int(data.get("ticks_in_beat") or 0) if synced else 0,
        "daemon_online": daemon_online,
    }


def looper_hud_should_show(snapshot: dict, *, user_enabled: bool = True) -> bool:
    """Header badge when SL grid is active or pedal tempo is available."""
    if not user_enabled:
        return False
    sl = snapshot.get("sl") or {}
    if sl.get("active") or sl.get("has_master"):
        return True
    if not snapshot.get("connected"):
        return False
    if snapshot.get("running") and snapshot.get("bpm") is not None:
        return True
    return snapshot.get("bpm") is not None


def looper_hud_label(snapshot: dict) -> str:
    """Compact header label — SL beat (1/4) when grid master loop is playing, else BPM."""
    sl = snapshot.get("sl") or {}
    if sl.get("active") and sl.get("beat") is not None:
        return f"{sl['beat']}/4"
    bpm = snapshot.get("bpm")
    if bpm is not None:
        return str(int(bpm))
    if sl.get("has_master"):
        return "LOOP"
    return ""
