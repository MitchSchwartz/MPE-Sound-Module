"""Track sounding MIDI notes for governor fade and deferred poly-limit drops."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from patch_browser.json_store import atomic_write_json, read_json_dict
from patch_browser.midi_sync import is_note_off, is_note_on
from patch_browser.mpe_run_dir import run_dir

VOICE_TRACKER_FILE = run_dir() / "poly-voice-tracker.json"
FADE_REQUEST_FILE = run_dir() / "governor-fade-request.json"


def fade_actuation_enabled() -> bool:
    return os.environ.get("MPE_POLY_GOVERNOR_FADE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


@dataclass(frozen=True)
class ActiveNote:
    channel: int
    note: int
    started_at: float

    def key(self) -> tuple[int, int]:
        return (self.channel, self.note)

    def to_json(self) -> dict:
        return {
            "channel": self.channel,
            "note": self.note,
            "started_at": self.started_at,
        }

    @classmethod
    def from_json(cls, raw: dict) -> ActiveNote | None:
        try:
            return cls(
                channel=int(raw["channel"]),
                note=int(raw["note"]),
                started_at=float(raw["started_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


class PolyVoiceTracker:
    """In-process note ledger mirrored to tmpfs for the poly governor daemon."""

    def __init__(self) -> None:
        self._notes: dict[tuple[int, int], ActiveNote] = {}

    def active_count(self) -> int:
        return len(self._notes)

    def observe_message(self, message: list[int]) -> bool:
        """Update ledger from a MIDI message Surge will receive. Returns True if changed."""
        if len(message) < 3 or message[0] < 0x80:
            return False
        channel = message[0] & 0x0F
        note = int(message[1])
        key = (channel, note)
        now = time.monotonic()
        if is_note_on(message):
            self._notes[key] = ActiveNote(channel=channel, note=note, started_at=now)
            return True
        if is_note_off(message):
            if key in self._notes:
                del self._notes[key]
                return True
        return False

    def notes_to_release(self, count: int) -> list[tuple[int, int]]:
        if count <= 0:
            return []
        ordered = sorted(self._notes.values(), key=lambda item: item.started_at)
        return [(item.channel, item.note) for item in ordered[:count]]

    def snapshot(self) -> dict:
        return {
            "active_count": self.active_count(),
            "notes": [item.to_json() for item in self._notes.values()],
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


def write_fade_request(*, release_count: int, reason: str) -> None:
    atomic_write_json(
        FADE_REQUEST_FILE,
        {
            "request_id": time.monotonic(),
            "release_count": max(0, int(release_count)),
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
