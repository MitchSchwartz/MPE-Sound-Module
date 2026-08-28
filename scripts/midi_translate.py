"""Classic (channel-based) MIDI → MPE, as pure functions.

Plan: docs/CLASSIC-MIDI-PLAN.md. This is phase 1 — the whole translation
vocabulary with no I/O, so it is testable without a Pi, without hardware and
without an audio graph. The router daemon (phase 2) owns ports and threads and
calls into this; nothing here opens anything.

Why translate rather than switch Surge's mode: Surge exposes no OSC path to
change MPE mode or bend range (plan §7.1), so a mode switch means restarting
Surge and dropping audio, and two controller kinds could never work at once.

Three findings from the Surge source shape the rules below, and each is a
"do not "cleverly" fix this later" note:

  * Master-channel pitch bend is a dead path in Surge — the configured
    48-semitone range applies to MEMBER channels only (plan §7.3). Bend is
    therefore written to member channels, never to the master.
  * Surge releases a note only when its member channel AND the master channel
    are both un-held (plan §7.7), so CC64 goes to the master and Surge owns
    sustain. This module deliberately does NOT defer note-offs; doing so would
    be a second mechanism racing Surge's and is how stuck notes are made.
  * Master-channel notes are NOT filtered by Surge (plan §7.2), so a
    misclassified device plays with the wrong bend depth rather than falling
    silent. That is why classification is visible in the UI, and why this
    module never silently guesses a bend range: an undeclared range is ±2 by
    convention (plan §7.5) and is stated, not inferred.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# --- MIDI ------------------------------------------------------------------
NOTE_OFF = 0x80
NOTE_ON = 0x90
POLY_AFTERTOUCH = 0xA0
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
CHANNEL_PRESSURE = 0xD0
PITCH_BEND = 0xE0

CC_BANK_MSB = 0
CC_MOD_WHEEL = 1
CC_DATA_ENTRY_MSB = 6
CC_EXPRESSION = 11
CC_SUSTAIN = 64
CC_TIMBRE = 74
CC_DATA_ENTRY_LSB = 38
CC_RPN_LSB = 100
CC_RPN_MSB = 101
CC_ALL_SOUND_OFF = 120
CC_RESET_ALL_CONTROLLERS = 121
CC_ALL_NOTES_OFF = 123

BEND_CENTRE = 8192
BEND_MIN = 0
BEND_MAX = 16383

# Zone-wide for a classic device: it has one of each, applying to everything.
BROADCAST_CCS = (CC_MOD_WHEEL, CC_EXPRESSION, CC_TIMBRE)

# --- MPE -------------------------------------------------------------------
# Lower zone: master is MIDI channel 1 (index 0), members are 2..16 (1..15).
MASTER_CHANNEL = 0
MEMBER_CHANNELS: tuple[int, ...] = tuple(range(1, 16))

# Surge's member-channel range, set by --mpe-pitch-bend-range=48. If that flag
# changes, this must change with it or every bend is wrong by the ratio.
SURGE_MEMBER_BEND_SEMITONES = float(
    os.environ.get("MPE_SURGE_BEND_SEMITONES", "48")
)
# What a classic controller means by full-scale bend when it never says.
# GM convention; most keyboards never transmit RPN 0/0 (plan §7.5).
DEFAULT_CLASSIC_BEND_SEMITONES = float(
    os.environ.get("MPE_CLASSIC_BEND_SEMITONES", "2")
)

STEAL_OLDEST = "oldest"
STEAL_NEWEST = "newest"
STEAL_NEVER = "never"


def clamp_bend(value: int) -> int:
    return max(BEND_MIN, min(BEND_MAX, value))


def scale_bend(value14: int, in_semitones: float, out_semitones: float) -> int:
    """Re-express a 14-bit bend so it means the same pitch on a wider range.

    A classic ±2 bend arriving at a synth configured for ±48 is 24× too wide
    unless it is scaled here — that is the headline symptom in plan §1.A.
    """
    if out_semitones <= 0:
        return BEND_CENTRE
    offset = value14 - BEND_CENTRE
    return clamp_bend(round(BEND_CENTRE + offset * (in_semitones / out_semitones)))


@dataclass
class _Voice:
    note: int
    channel: int
    started: int


@dataclass
class ClassicToMpe:
    """One classic device's translation state.

    Pure: ``translate`` returns the messages to send and touches nothing else.
    Time is a monotonically increasing tick, not a clock, so tests are exact.
    """

    member_channels: tuple[int, ...] = MEMBER_CHANNELS
    bend_semitones: float = DEFAULT_CLASSIC_BEND_SEMITONES
    target_bend_semitones: float = SURGE_MEMBER_BEND_SEMITONES
    steal: str = STEAL_OLDEST

    _tick: int = field(default=0, init=False)
    _active: dict[int, _Voice] = field(default_factory=dict, init=False)
    # Channel -> tick it was released. Oldest release is reused first, so a
    # still-ringing tail is not re-modulated by the next note (plan §7.6).
    _released: dict[int, int] = field(default_factory=dict, init=False)
    _bend14: int = field(default=BEND_CENTRE, init=False)
    _rpn: tuple[int, int] | None = field(default=None, init=False)

    # -- allocation ---------------------------------------------------------
    def _free_channel(self) -> int | None:
        in_use = {v.channel for v in self._active.values()}
        free = [c for c in self.member_channels if c not in in_use]
        if not free:
            return None
        # Never-used channels first, then longest-released.
        return min(free, key=lambda c: (self._released.get(c, -1), c))

    def _steal(self) -> tuple[int, list[list[int]]] | None:
        if self.steal == STEAL_NEVER or not self._active:
            return None
        if self.steal == STEAL_NEWEST:
            victim = max(self._active.values(), key=lambda v: v.started)
        else:
            victim = min(self._active.values(), key=lambda v: v.started)
        out = self._release(victim.note)
        return victim.channel, out

    def _release(self, note: int) -> list[list[int]]:
        voice = self._active.pop(note, None)
        if voice is None:
            return []
        self._released[voice.channel] = self._tick
        return [[NOTE_OFF | voice.channel, note, 0]]

    # -- translation --------------------------------------------------------
    def translate(self, msg: list[int]) -> list[list[int]]:
        """One inbound message -> zero or more outbound messages."""
        if not msg:
            return []
        self._tick += 1
        status = msg[0]
        if status >= 0xF0:  # clock, transport, sysex — untouched
            return [list(msg)]

        kind = status & 0xF0
        if kind == NOTE_ON and len(msg) >= 3 and msg[2] > 0:
            return self._note_on(msg[1], msg[2])
        if kind == NOTE_OFF or (kind == NOTE_ON and len(msg) >= 3):
            return self._release(msg[1])
        if kind == PITCH_BEND and len(msg) >= 3:
            return self._pitch_bend(msg[1], msg[2])
        if kind == CHANNEL_PRESSURE and len(msg) >= 2:
            return [
                [CHANNEL_PRESSURE | v.channel, msg[1]] for v in self._active.values()
            ]
        if kind == POLY_AFTERTOUCH and len(msg) >= 3:
            # Per-note pressure is exactly what MPE expresses per member
            # channel — the one classic message that maps without loss.
            voice = self._active.get(msg[1])
            return [[CHANNEL_PRESSURE | voice.channel, msg[2]]] if voice else []
        if kind == CONTROL_CHANGE and len(msg) >= 3:
            return self._control_change(msg[1], msg[2])
        if kind == PROGRAM_CHANGE:
            return []  # dropped by decision — plan §5, OPEN-1
        return [list(msg)]

    def _note_on(self, note: int, velocity: int) -> list[list[int]]:
        out: list[list[int]] = []
        if note in self._active:  # retrigger: free the old channel first
            out += self._release(note)
        channel = self._free_channel()
        if channel is None:
            stolen = self._steal()
            if stolen is None:
                return out  # STEAL_NEVER and full: drop the note, do not hang
            channel, steal_out = stolen
            out += steal_out
        self._active[note] = _Voice(note=note, channel=channel, started=self._tick)
        self._released.pop(channel, None)
        # The zone's current bend must apply to a note that arrives mid-bend,
        # or it sounds at the wrong pitch until the next bend message.
        if self._bend14 != BEND_CENTRE:
            out.append(self._bend_message(channel))
        out.append([NOTE_ON | channel, note, velocity])
        return out

    def _bend_message(self, channel: int) -> list[int]:
        scaled = scale_bend(
            self._bend14, self.bend_semitones, self.target_bend_semitones
        )
        return [PITCH_BEND | channel, scaled & 0x7F, (scaled >> 7) & 0x7F]

    def _pitch_bend(self, lsb: int, msb: int) -> list[list[int]]:
        self._bend14 = (msb << 7) | lsb
        # Member channels only. Never the master (plan §7.3).
        return [self._bend_message(v.channel) for v in self._active.values()]

    def _control_change(self, cc: int, value: int) -> list[list[int]]:
        if cc == CC_SUSTAIN:
            # Surge holds the whole zone off the master channel, and owns the
            # deferral. Do not also defer note-offs here (plan §7.7).
            return [[CONTROL_CHANGE | MASTER_CHANNEL, CC_SUSTAIN, value]]
        if cc in (CC_ALL_NOTES_OFF, CC_ALL_SOUND_OFF, CC_RESET_ALL_CONTROLLERS):
            return self.all_notes_off()
        if cc == CC_RPN_MSB:
            self._rpn = (value, self._rpn[1] if self._rpn else 0)
            return []
        if cc == CC_RPN_LSB:
            self._rpn = (self._rpn[0] if self._rpn else 0, value)
            return []
        if cc in (CC_DATA_ENTRY_MSB, CC_DATA_ENTRY_LSB):
            if self._rpn == (0, 0):  # RPN 0/0 = pitch bend sensitivity
                if cc == CC_DATA_ENTRY_MSB:
                    self.bend_semitones = float(value)
                return []  # consumed: the range is ours, not Surge's
            return []
        if cc in BROADCAST_CCS:
            return [
                [CONTROL_CHANGE | v.channel, cc, value] for v in self._active.values()
            ]
        # Anything else is zone-wide for a classic device.
        return [[CONTROL_CHANGE | MASTER_CHANNEL, cc, value]]

    # -- safety -------------------------------------------------------------
    def all_notes_off(self) -> list[list[int]]:
        """Every sounding note released. For unplug, restart and panic.

        The stuck-note failure of any channel-allocating translator is a
        note-off for a note whose channel was stolen (plan §6, risk 4), so
        this exists and must be called on every teardown path.
        """
        out = [[NOTE_OFF | v.channel, v.note, 0] for v in self._active.values()]
        for voice in list(self._active.values()):
            self._released[voice.channel] = self._tick
        self._active.clear()
        self._bend14 = BEND_CENTRE
        return out

    @property
    def active_notes(self) -> dict[int, int]:
        """note -> member channel. For the UI and for tests."""
        return {n: v.channel for n, v in self._active.items()}


def mpe_configuration_message(
    member_count: int, *, master_channel: int = MASTER_CHANNEL
) -> list[list[int]]:
    """The MCM a zone is declared with — RPN 6 (plan §7.4).

    Emitted by the router, and the thing to recognise INBOUND when deciding a
    device is MPE and needs no translation at all.
    """
    if not 0 <= member_count <= 15:
        raise ValueError(f"member_count out of range: {member_count}")
    return [
        [CONTROL_CHANGE | master_channel, CC_RPN_MSB, 6],
        [CONTROL_CHANGE | master_channel, CC_RPN_LSB, 0],
        [CONTROL_CHANGE | master_channel, CC_DATA_ENTRY_MSB, member_count],
    ]
