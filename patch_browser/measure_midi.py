"""The one MIDI path for measurement load generators — quiet and member-only.

scripts/midi-load-hold.py and scripts/midi-load.py both send through here.
Two faults lived in their own copies until 2026-10-12:

* Velocity 100. Measurement needs voices, not level; AGENTS.md "Audio output
  safety" says use the quietest level that proves the thing. The default is
  DEFAULT_VELOCITY; `--velocity` changes it.
* Voice v went to status nibble v + 1, so a 16th voice wrapped to nibble 0:
  the MPE lower-zone manager channel (MIDI 1), where Surge reads zone-wide
  messages. Voices now map onto the member channels from pressure_midi (the
  remapper's authority), and a voice count the member channels cannot address
  is refused, not wrapped.

`Sender` is the boundary: every message passes its channel check, so no code
path, including cleanup, can reach the manager channel.
"""

from __future__ import annotations

import argparse
import math
import time

from patch_browser.pressure_midi import MPE_MEMBER_CHANNEL_MAX, MPE_MEMBER_CHANNEL_MIN

#: Status low nibble of the lower-zone manager channel (MIDI channel 1).
MANAGER_CHANNEL = 0

#: Status low nibbles a voice may use: MIDI channels 2..16.
MEMBER_CHANNELS = tuple(range(MPE_MEMBER_CHANNEL_MIN, MPE_MEMBER_CHANNEL_MAX + 1))

#: One voice per member channel; more would need two notes on one channel.
MAX_VOICES = len(MEMBER_CHANNELS)

#: The 2026-10-12 capacity run used 20 and stayed at peak 0.080 post-FX.
DEFAULT_VELOCITY = 20


class ManagerChannelError(ValueError):
    """A measurement message addressed the manager channel or no valid channel."""


def member_channel(voice: int) -> int:
    """Status nibble for voice index 0..MAX_VOICES-1. Never wraps."""
    if not 0 <= voice < MAX_VOICES:
        raise ValueError(f"voice {voice} has no member channel (0..{MAX_VOICES - 1})")
    return MEMBER_CHANNELS[voice]


def check_voices(voices: int) -> int:
    if not 1 <= voices <= MAX_VOICES:
        raise ValueError(
            f"VOICES must be 1..{MAX_VOICES} (one per MPE member channel), got {voices}"
        )
    return voices


def check_velocity(velocity: int) -> int:
    if not 1 <= velocity <= 127:
        raise ValueError(f"velocity must be 1..127, got {velocity}")
    return velocity


class Sender:
    """Wraps an rtmidi-like output. Refuses anything not on a member channel."""

    def __init__(self, out, *, velocity: int = DEFAULT_VELOCITY) -> None:
        self._out = out
        self.velocity = check_velocity(velocity)

    def _send(self, status: int, channel: int, *data: int) -> None:
        if channel not in MEMBER_CHANNELS:
            raise ManagerChannelError(f"channel nibble {channel} is not an MPE member channel")
        self._out.send_message([status | channel, *data])

    def note_on(self, channel: int, note: int) -> None:
        self._send(0x90, channel, note & 0x7F, self.velocity)

    def note_off(self, channel: int, note: int) -> None:
        self._send(0x80, channel, note & 0x7F, 0)

    def pressure(self, channel: int, value: int) -> None:
        self._send(0xD0, channel, max(0, min(127, int(value))))

    def bend(self, channel: int, value: int) -> None:
        value = max(0, min(16383, int(value)))
        self._send(0xE0, channel, value & 0x7F, (value >> 7) & 0x7F)

    def all_notes_off(self, channel: int) -> None:
        self._send(0xB0, channel, 123, 0)


def add_velocity_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--velocity",
        type=int,
        default=DEFAULT_VELOCITY,
        help=f"note-on velocity 1..127 (default {DEFAULT_VELOCITY}, quiet on purpose)",
    )


def open_through_port(rtmidi_module):
    """Open the ALSA Midi Through port. Returns (out, port_name) or raises."""
    out = rtmidi_module.MidiOut()
    ports = out.get_ports()
    idx = next((i for i, p in enumerate(ports) if "Midi Through" in p), None)
    if idx is None:
        raise RuntimeError(f"no Surge MIDI port in {ports}")
    out.open_port(idx)
    return out, ports[idx]


BASE_NOTE = 48
CTRL_HZ = 50


def _expression(sender: Sender, channel: int, voice: int, elapsed: float) -> None:
    """Deterministic MPE modulation: the same curves every run."""
    sender.pressure(channel, 64 + 60 * math.sin(elapsed * 2.0 + voice))
    sender.bend(channel, 8192 + 3000 * math.sin(elapsed * 1.3 + voice * 0.7))


def hold(sender: Sender, voices: int, secs: float, *, clock=time.monotonic,
         sleep=time.sleep) -> None:
    """Hold `voices` notes, one per member channel, with modulation, then release."""
    check_voices(voices)
    channels = [member_channel(v) for v in range(voices)]
    notes = [BASE_NOTE + (v * 2) % 24 for v in range(voices)]
    try:
        for ch, note in zip(channels, notes):
            sender.note_on(ch, note)
        t0 = clock()
        while True:
            elapsed = clock() - t0
            if elapsed >= secs:
                break
            for v, ch in enumerate(channels):
                _expression(sender, ch, v, elapsed)
            sleep(1.0 / CTRL_HZ)
    finally:
        for ch, note in zip(channels, notes):
            sender.note_off(ch, note)
            sender.all_notes_off(ch)


def stagger(sender: Sender, voices: int, secs: float, *, step_s: float = 0.5,
            clock=time.monotonic, sleep=time.sleep) -> None:
    """Re-trigger each voice in turn every `step_s * voices` seconds (midi-load.py)."""
    check_voices(voices)
    channels = [member_channel(v) for v in range(voices)]
    last_note = [0.0] * voices
    playing: list[int | None] = [None] * voices
    t0 = clock()
    try:
        while True:
            elapsed = clock() - t0
            if elapsed >= secs:
                break
            for v, ch in enumerate(channels):
                if elapsed - last_note[v] >= step_s * voices:
                    if playing[v] is not None:
                        sender.note_off(ch, playing[v])
                    note = BASE_NOTE + (int(elapsed / step_s) + v * 5) % 24
                    sender.note_on(ch, note)
                    playing[v] = note
                    last_note[v] = elapsed
                _expression(sender, ch, v, elapsed)
            sleep(1.0 / CTRL_HZ)
    finally:
        for v, ch in enumerate(channels):
            if playing[v] is not None:
                sender.note_off(ch, playing[v])
            sender.all_notes_off(ch)


def release_on_sigterm() -> None:
    """Turn SIGTERM into SystemExit so hold()/stagger() release their notes.

    The harnesses stop a generator with `kill`; Python's default SIGTERM action
    skips `finally`, which would leave every voice sounding.
    """
    import signal
    import sys

    signal.signal(signal.SIGTERM, lambda _sig, _frm: sys.exit(143))
