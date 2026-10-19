"""Sustain pedal owned by the remapper, so pedal-held notes fade like a piano.

Surge's own sustain holds a note until the pedal comes up. On an organ or pad
patch (amp sustain 1.0) that is forever, so chords added under the pedal stack
voices and the level climbs: measured 3 -> ~13 voices, about +7 dB. Surge has
no per-note volume over MIDI, so the remapper stops forwarding CC 64 and keeps
the pedal itself. A key released under the pedal is held for a while, then
gets its note-off and the patch's release envelope fades it.

How long depends on the pitch, as on a piano, whose bass strings ring far
longer under the damper pedal than its treble: ``MPE_PEDAL_FADE_LOW_S`` at
MIDI note 21 (A0), ``MPE_PEDAL_FADE_HIGH_S`` at 108 (C8), geometric in
between, clamped outside. With the defaults (8 s, 2 s) middle C holds 4.30 s.

Precedence (``hold_register``): ``MPE_PEDAL_FADE_S=0`` turns the fade off. If
``MPE_PEDAL_FADE_S`` is set and neither LOW nor HIGH is, every note holds that
long (the flat hold this module had first). Otherwise the curve.

Pedal up does not drop everything at once either. The retired 3-pedal setup
(``SustainFadeController`` in scripts/pedal-to-osc.py) ramped CC 64 down a
fixed curve over 2 s. Surge reads CC 64 as on/off, so here the same curve
decides how many pedal-held notes are still held at each step, and the rest get
their note-offs, oldest first, across ``MPE_PEDAL_RELEASE_FADE_S``. A pedal
press inside that window takes the notes not yet released back into the hold.

``PedalFade`` is pure: every method takes ``now`` and returns the messages to
send, in order. It works on the stream Surge receives (after translation), so
channels are the ones each note was actually sent on. The remapper is its only
caller; ``MPE_PEDAL_FADE_S=0`` means the remapper never builds one and CC 64 is
forwarded as before.

Invariant: each note-on it forwards is followed by at most one note-off, from
whichever comes first of the key, the hold deadline, the pedal-up window, a
re-strike, panic, shutdown or a shed (``forget``).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from patch_browser.midi_sync import is_note_off, is_note_on

PEDAL_FADE_ENV = "MPE_PEDAL_FADE_S"
DEFAULT_PEDAL_FADE_S = 3.0
FADE_LOW_ENV = "MPE_PEDAL_FADE_LOW_S"
FADE_HIGH_ENV = "MPE_PEDAL_FADE_HIGH_S"
DEFAULT_FADE_LOW_S = 8.0
DEFAULT_FADE_HIGH_S = 2.0
LOW_NOTE = 21  # A0, the piano's lowest key
HIGH_NOTE = 108  # C8, its highest
RELEASE_FADE_ENV = "MPE_PEDAL_RELEASE_FADE_S"
# 0: pedal up releases every pedal-held note at once, as a piano's dampers
# all fall together. >0 spreads them (pedal-to-osc.py's old CC 64 ramp).
DEFAULT_RELEASE_FADE_S = 0.0
RELEASE_CURVE_ENV = "MPE_PEDAL_RELEASE_CURVE"
# CC 64 values per step, copied from SustainFadeController.get_fade_steps.
# Read as the share of pedal-held notes still held at each step (value / 127).
RELEASE_CURVES: dict[str, tuple[int, ...]] = {
    "linear": (127, 85, 58, 38, 25, 16, 10, 6, 3, 1, 0),
    "exponential": (127, 90, 64, 45, 32, 22, 16, 11, 8, 5, 3, 2, 1, 0),
}
SUSTAIN_CC = 64
ALL_SOUND_OFF_CC = 120
ALL_NOTES_OFF_CC = 123
# MPE lower zone: the manager channel's pedal holds every channel; a member
# channel's own CC 64 holds only itself. Same rule as poly_voice_tracker.
MANAGER_CHANNEL = 0

Key = tuple[int, int]  # (channel, note)


def _seconds(raw: str | None, default: float) -> float:
    """Unset or unparseable is ``default``; zero, negative or NaN is 0 (off)."""
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value != value or value <= 0:
        return 0.0
    return value


def pedal_fade_seconds(raw: str | None = None) -> float:
    """``MPE_PEDAL_FADE_S``: seconds a released key rings under the pedal."""
    return _seconds(os.environ.get(PEDAL_FADE_ENV) if raw is None else raw,
                    DEFAULT_PEDAL_FADE_S)


def register_hold_seconds(note: int, low_s: float, high_s: float) -> float:
    """Hold for ``note``: ``low_s`` at A0, ``high_s`` at C8, log-linear
    between (equal ratio per semitone), clamped outside 21..108."""
    n = min(max(note, LOW_NOTE), HIGH_NOTE)
    return low_s * (high_s / low_s) ** ((n - LOW_NOTE) / (HIGH_NOTE - LOW_NOTE))


def hold_register(
    fade_raw: str | None = None,
    low_raw: str | None = None,
    high_raw: str | None = None,
) -> tuple[float, float] | None:
    """``(low_s, high_s)`` for the pitch curve, or None for a flat hold.

    Flat only when ``MPE_PEDAL_FADE_S`` is set and neither LOW nor HIGH is,
    so a config written for the flat hold keeps it. A LOW or HIGH that is
    unset, unparseable or not positive takes its default.
    """
    env = os.environ
    fade_raw = env.get(PEDAL_FADE_ENV) if fade_raw is None else fade_raw
    low_raw = env.get(FADE_LOW_ENV) if low_raw is None else low_raw
    high_raw = env.get(FADE_HIGH_ENV) if high_raw is None else high_raw
    if (fade_raw or "").strip() and not (low_raw or "").strip() \
            and not (high_raw or "").strip():
        return None
    low_s = _seconds(low_raw, DEFAULT_FADE_LOW_S) or DEFAULT_FADE_LOW_S
    high_s = _seconds(high_raw, DEFAULT_FADE_HIGH_S) or DEFAULT_FADE_HIGH_S
    return (low_s, high_s)


def release_fade_seconds(raw: str | None = None) -> float:
    """``MPE_PEDAL_RELEASE_FADE_S``: pedal-up window; 0 = all at once."""
    return _seconds(os.environ.get(RELEASE_FADE_ENV) if raw is None else raw,
                    DEFAULT_RELEASE_FADE_S)


def release_curve(raw: str | None = None) -> tuple[int, ...]:
    """``MPE_PEDAL_RELEASE_CURVE``: linear (default) or exponential."""
    name = (os.environ.get(RELEASE_CURVE_ENV) if raw is None else raw) or ""
    return RELEASE_CURVES.get(name.strip().lower(), RELEASE_CURVES["linear"])


def release_schedule(
    count: int, start: float, window_s: float, curve: tuple[int, ...]
) -> list[float]:
    """When each of ``count`` notes (oldest first) gets its note-off.

    At curve step ``i`` (time ``start + i * window_s / (len(curve) - 1)``)
    ``round(count * curve[i] / 127)`` notes are still held. Rounding, not
    floor: a lone note lets go at the step where the old CC ramp crossed
    Surge's on/off threshold of 64 (58, the second step, 0.4 s at 2 s).
    """
    if count <= 0:
        return []
    if window_s <= 0 or len(curve) < 2:
        return [start] * count
    step_s = window_s / (len(curve) - 1)
    times: list[float] = []
    for i, value in enumerate(curve):
        hold = math.floor(count * value / 127 + 0.5) if i < len(curve) - 1 else 0
        while len(times) < count - hold:
            times.append(start + i * step_s)
    return times


@dataclass
class _PedalHeld:
    note_off: list[int]  # the key's own note-off, sent as-is when it is due
    started_at: float  # its note-on: "oldest first" in the pedal-up window
    deadline: float  # release + hold_s(note)
    release_at: float | None = None  # set while the pedal-up window runs

    def due_at(self) -> float:
        if self.release_at is None:
            return self.deadline
        return min(self.deadline, self.release_at)


class PedalFade:
    def __init__(
        self,
        fade_s: float,
        release_fade_s: float = 0.0,
        curve: tuple[int, ...] = RELEASE_CURVES["linear"],
        register: tuple[float, float] | None = None,
    ) -> None:
        # fade_s: the flat hold, used when register is None.
        # register: (hold at A0, hold at C8); see register_hold_seconds.
        self.fade_s = float(fade_s)
        self.register = register
        self.release_fade_s = float(release_fade_s)
        self.curve = tuple(curve)
        self._pedals: set[int] = set()
        # Keys down: note-on forwarded, no note-off yet -> note-on time.
        self._held: dict[Key, float] = {}
        # Keys up under the pedal (or in the pedal-up window), still sounding.
        self._pedal_held: dict[Key, _PedalHeld] = {}
        # Keys down whose note-off someone else already sent (a shed): the
        # key's own note-off is swallowed, or it would be the second one.
        self._released_early: set[Key] = set()
        self._next_deadline: float | None = None

    @classmethod
    def from_env(cls) -> PedalFade | None:
        fade_s = pedal_fade_seconds()
        if fade_s <= 0:
            return None
        return cls(fade_s, release_fade_seconds(), release_curve(),
                   hold_register())

    def hold_s(self, note: int) -> float:
        """Seconds a key released under the pedal keeps sounding."""
        if self.register is None:
            return self.fade_s
        return register_hold_seconds(note, *self.register)

    def hold_label(self) -> str:
        if self.register is None:
            return f"{self.fade_s:g} s"
        low_s, high_s = self.register
        return f"{low_s:g} s at A0 to {high_s:g} s at C8"

    # -- queries -------------------------------------------------------------
    def pedal_held(self) -> frozenset[Key]:
        return frozenset(self._pedal_held)

    def next_deadline(self) -> float | None:
        return self._next_deadline

    def _pedal_holds(self, channel: int) -> bool:
        return MANAGER_CHANNEL in self._pedals or channel in self._pedals

    def _recompute_deadline(self) -> None:
        self._next_deadline = min(
            (held.due_at() for held in self._pedal_held.values()), default=None
        )

    def _release(self, keys) -> list[list[int]]:
        out = [self._pedal_held.pop(key).note_off for key in keys]
        if out:
            self._recompute_deadline()
        return out

    # -- the stream ----------------------------------------------------------
    def filter(self, message: list[int], now: float) -> list[list[int]]:
        """What to send Surge in place of ``message`` (possibly nothing)."""
        if len(message) < 3 or message[0] < 0x80 or message[0] >= 0xF0:
            return [message]
        kind = message[0] & 0xF0
        channel = message[0] & 0x0F
        if kind == 0xB0:
            cc = message[1]
            if cc == SUSTAIN_CC:
                return self._pedal(channel, down=message[2] >= 64, now=now)
            if cc in (ALL_NOTES_OFF_CC, ALL_SOUND_OFF_CC):
                return self._panic(channel) + [message]
            return [message]
        key = (channel, int(message[1]))
        if is_note_on(message):
            # Re-strike: an earlier instance of this pitch still ringing under
            # the pedal ends first, on whatever channel it was sent on, so the
            # pedal never stacks two of the same note.
            out = self._release(
                [k for k in self._pedal_held if k[1] == key[1]]
            )
            self._released_early.discard(key)
            self._held[key] = now
            out.append(message)
            return out
        if is_note_off(message):
            if key in self._released_early:
                self._released_early.discard(key)
                return []
            if key in self._pedal_held:
                # Already up: this is a duplicate. The pending one stands.
                return []
            if key not in self._held:
                # Never saw its note-on (e.g. this process restarted under a
                # held key). Pass it so the note cannot stick.
                return [message]
            started_at = self._held.pop(key)
            if self._pedal_holds(channel):
                held = _PedalHeld(
                    note_off=list(message),
                    started_at=started_at,
                    deadline=now + self.hold_s(key[1]),
                )
                self._pedal_held[key] = held
                if self._next_deadline is None or held.deadline < self._next_deadline:
                    self._next_deadline = held.deadline
                return []
            return [message]
        return [message]

    def _pedal(self, channel: int, *, down: bool, now: float) -> list[list[int]]:
        # CC 64 never reaches Surge: its own hold would undo the fade.
        if down:
            self._pedals.add(channel)
            # Back into the hold: notes the pedal-up window had not reached
            # yet stop counting down to release, and keep their deadline.
            for key, held in self._pedal_held.items():
                if held.release_at is not None and self._pedal_holds(key[0]):
                    held.release_at = None
            self._recompute_deadline()
            return []
        self._pedals.discard(channel)
        freed = sorted(
            (
                (held.started_at, key)
                for key, held in self._pedal_held.items()
                if held.release_at is None and not self._pedal_holds(key[0])
            ),
        )
        times = release_schedule(len(freed), now, self.release_fade_s, self.curve)
        for (_, key), at in zip(freed, times):
            self._pedal_held[key].release_at = at
        self._recompute_deadline()
        return self.due(now)

    def _panic(self, channel: int) -> list[list[int]]:
        """All-notes-off on ``channel`` (the manager channel: every channel).

        Pedal-held notes get their note-off here, explicitly, so the note
        ledger (which does not read CC 123) drops them too. Keys still down
        are left alone: their own note-off comes from the key.
        """
        return self._release(
            [
                k
                for k in self._pedal_held
                if channel == MANAGER_CHANNEL or k[0] == channel
            ]
        )

    def due(self, now: float) -> list[list[int]]:
        """Note-offs whose time has come, oldest first. One compare if none."""
        if self._next_deadline is None or now < self._next_deadline:
            return []
        ready = sorted(
            (held.due_at(), held.started_at, key)
            for key, held in self._pedal_held.items()
            if held.due_at() <= now
        )
        return self._release([key for _, _, key in ready])

    def forget(self, channel: int, note: int) -> None:
        """Someone else (the governor's shed) sent this note's note-off."""
        key = (channel, note)
        if key in self._pedal_held:
            del self._pedal_held[key]
            self._recompute_deadline()
        elif key in self._held:
            del self._held[key]
            self._released_early.add(key)

    def release_all(self) -> list[list[int]]:
        """Pedal up everywhere, every pedal-held note released now. For input
        teardown and process exit; keys still down keep their own note-off."""
        self._pedals.clear()
        return self._release(list(self._pedal_held))
