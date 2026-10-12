"""Track sounding MIDI notes for the poly governor, and decide which to shed.

The pressure remapper is the only process that sees every note before Surge, so
it owns this ledger and is the only sender of shed note-offs. The governor
writes a shed request (the limit it just set) to tmpfs; the remapper applies
``plan_shed`` to its ledger. ``plan_shed`` is the one place the shed policy lives.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from typing import Iterable

from patch_browser.json_store import atomic_write_json, read_json_dict
from patch_browser.midi_sync import is_note_off, is_note_on
from patch_browser.mpe_run_dir import run_dir

VOICE_TRACKER_FILE = run_dir() / "poly-voice-tracker.json"
FADE_REQUEST_FILE = run_dir() / "governor-fade-request.json"

# A note younger than this is never shed: its attack has barely started, and
# cutting it reads as the instrument dropping the note just played.
SHED_MIN_AGE_S = 0.1
# A plain note-off, so the patch's own release tail plays. 64 is the MIDI
# convention for "no release velocity".
SHED_NOTE_OFF_VELOCITY = 64
SUSTAIN_CC = 64
# MPE lower zone: the manager channel is MIDI channel 1. Its sustain pedal
# holds every member channel; a member channel's own CC 64 holds only itself.
MANAGER_CHANNEL = 0


def _env_on(key: str) -> bool:
    return os.environ.get(key, "1").strip().lower() not in ("0", "false", "no", "off")


def fade_actuation_enabled() -> bool:
    """Master switch for the note ledger (and so for shedding and deferral)."""
    return _env_on("MPE_POLY_GOVERNOR_FADE")


def shed_enabled() -> bool:
    """``MPE_POLY_SHED`` (default on): a step-down below the sounding count
    releases the oldest notes. Off: the limit only gates new allocations, as
    before 2026-10-12. Needs the ledger (``MPE_POLY_GOVERNOR_FADE``, default on)."""
    return fade_actuation_enabled() and _env_on("MPE_POLY_SHED")


@dataclass(frozen=True)
class ActiveNote:
    channel: int
    note: int
    started_at: float
    # Key released while a sustain pedal held it: still sounding, and a
    # note-off cannot release it, so it is never a shed candidate.
    sustained: bool = False

    def key(self) -> tuple[int, int]:
        return (self.channel, self.note)

    def to_json(self) -> dict:
        return {
            "channel": self.channel,
            "note": self.note,
            "started_at": self.started_at,
            "sustained": self.sustained,
        }

    @classmethod
    def from_json(cls, raw: dict) -> ActiveNote | None:
        try:
            return cls(
                channel=int(raw["channel"]),
                note=int(raw["note"]),
                started_at=float(raw["started_at"]),
                sustained=bool(raw.get("sustained", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class ShedPlan:
    """What one pass of a shed request does."""

    release: tuple[tuple[int, int], ...]
    blocked_by_sustain: int
    too_young: int
    done: bool


def plan_shed(
    notes: Iterable[ActiveNote],
    *,
    limit: int,
    requested_at: float,
    now: float,
    pedal_channels: Iterable[int] = (),
    min_age_s: float = SHED_MIN_AGE_S,
) -> ShedPlan:
    """The shed policy, and the only place it is decided.

    Release ``sounding - limit`` notes, oldest first, from the notes that were
    already sounding when the governor asked (``started_at <= requested_at``),
    skipping any a sustain pedal holds (a note-off would not free the voice)
    and any younger than ``min_age_s``. ``done`` is False only while a skipped
    young note could still age into eligibility, so the caller retries once
    it has; notes started after the request are never shed by it.
    """
    pedals = set(pedal_channels)
    sounding = list(notes)
    excess = len(sounding) - limit
    if excess <= 0:
        return ShedPlan(release=(), blocked_by_sustain=0, too_young=0, done=True)
    held_by_pedal = MANAGER_CHANNEL in pedals
    blocked = 0
    young: list[ActiveNote] = []
    eligible: list[ActiveNote] = []
    for item in sounding:
        if item.started_at > requested_at:
            continue
        if item.sustained or held_by_pedal or item.channel in pedals:
            blocked += 1
        elif now - item.started_at < min_age_s:
            young.append(item)
        else:
            eligible.append(item)
    eligible.sort(key=lambda item: item.started_at)
    release = tuple(item.key() for item in eligible[:excess])
    done = len(release) >= excess or not young
    return ShedPlan(
        release=release,
        blocked_by_sustain=blocked,
        too_young=len(young),
        done=done,
    )


class PolyVoiceTracker:
    """In-process note ledger mirrored to tmpfs for the poly governor daemon."""

    def __init__(self) -> None:
        self._notes: dict[tuple[int, int], ActiveNote] = {}
        self._pedals: set[int] = set()
        self._last_note_on_at: float | None = None

    def active_count(self) -> int:
        return len(self._notes)

    def pedal_channels(self) -> frozenset[int]:
        return frozenset(self._pedals)

    def _pedal_holds(self, channel: int) -> bool:
        return MANAGER_CHANNEL in self._pedals or channel in self._pedals

    def observe_message(self, message: list[int], *, now: float | None = None) -> bool:
        """Update ledger from a MIDI message Surge will receive. Returns True if changed."""
        if len(message) < 3 or message[0] < 0x80:
            return False
        if now is None:
            now = time.monotonic()
        channel = message[0] & 0x0F
        if message[0] & 0xF0 == 0xB0 and message[1] == SUSTAIN_CC:
            return self._observe_pedal(channel, down=message[2] >= 64)
        note = int(message[1])
        key = (channel, note)
        if is_note_on(message):
            self._notes[key] = ActiveNote(channel=channel, note=note, started_at=now)
            self._last_note_on_at = now
            return True
        if is_note_off(message):
            item = self._notes.get(key)
            if item is None:
                return False
            if self._pedal_holds(channel):
                if item.sustained:
                    return False
                self._notes[key] = replace(item, sustained=True)
            else:
                del self._notes[key]
            return True
        return False

    def _observe_pedal(self, channel: int, *, down: bool) -> bool:
        if down:
            if channel in self._pedals:
                return False
            self._pedals.add(channel)
            return True
        if channel not in self._pedals:
            return False
        self._pedals.discard(channel)
        for key, item in list(self._notes.items()):
            if item.sustained and not self._pedal_holds(item.channel):
                del self._notes[key]
        return True

    def plan_shed(self, *, limit: int, requested_at: float, now: float) -> ShedPlan:
        return plan_shed(
            self._notes.values(),
            limit=limit,
            requested_at=requested_at,
            now=now,
            pedal_channels=self._pedals,
        )

    def snapshot(self) -> dict:
        return {
            "active_count": self.active_count(),
            "notes": [item.to_json() for item in self._notes.values()],
            "pedals": sorted(self._pedals),
            "last_note_on_at": self._last_note_on_at,
            "updated_at": time.monotonic(),
        }

    def persist(self) -> None:
        atomic_write_json(VOICE_TRACKER_FILE, self.snapshot(), sort_keys=False)

    def load(self) -> None:
        data = read_json_dict(VOICE_TRACKER_FILE, label="poly-voice-tracker")
        self._notes.clear()
        for raw in data.get("notes", []):
            if not isinstance(raw, dict):
                continue
            item = ActiveNote.from_json(raw)
            if item is not None:
                self._notes[item.key()] = item


def read_active_voice_count() -> int:
    data = read_json_dict(VOICE_TRACKER_FILE, label="poly-voice-tracker")
    try:
        return max(0, int(data.get("active_count", 0)))
    except (TypeError, ValueError):
        return 0


def read_last_note_on_at() -> float | None:
    """Monotonic time of the last note-on the remapper sent Surge (CLOCK_MONOTONIC
    is system-wide, so it compares directly with the governor's clock)."""
    data = read_json_dict(VOICE_TRACKER_FILE, label="poly-voice-tracker")
    raw = data.get("last_note_on_at")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def write_shed_request(*, limit: int, reason: str) -> None:
    """Ask the remapper to bring the sounding count down to ``limit``."""
    now = time.monotonic()
    atomic_write_json(
        FADE_REQUEST_FILE,
        {
            "request_id": now,
            "requested_at": now,
            "limit": max(0, int(limit)),
            "reason": reason,
        },
        sort_keys=False,
    )


def read_fade_request() -> dict:
    return read_json_dict(FADE_REQUEST_FILE, label="governor-fade-request")


def clear_fade_request() -> None:
    try:
        FADE_REQUEST_FILE.unlink(missing_ok=True)
    except OSError:
        pass
