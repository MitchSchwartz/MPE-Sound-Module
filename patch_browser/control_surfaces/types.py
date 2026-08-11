"""Shared types for MIDI control surface maps (looper transport and friends)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LooperTransportAction(StrEnum):
    RECORD = "record"
    OVERDUB = "overdub"
    PLAY_STOP = "play_stop"
    CLEAR = "clear"


@dataclass(frozen=True)
class ControlSurfaceMap:
    """Immutable MIDI map for one hardware revision.

    New controllers add a row to their family registry — do not branch on variant
    strings in daemon code; pass a ``ControlSurfaceMap`` (or resolve via env/USB).
    """

    map_id: str
    label: str
    port_substrings: tuple[str, ...]
    usb_ids: tuple[str, ...]
    midi_channel: int
    scene_launch_notes: tuple[int, ...]
    shift_note: int | None
    grid_note_min: int
    grid_note_max: int
    looper_transport: dict[int, LooperTransportAction]
    mode_sysex: dict[str, bytes]
    track_button_notes: tuple[int, ...] = ()
    soft_key_notes: tuple[int, ...] = ()
    clip_stop_grid_notes: tuple[int, ...] = ()
    scene_launch_labels: tuple[str, ...] = ()
    fader_cc_numbers: tuple[int, ...] = ()
    master_fader_cc: int | None = None

    @property
    def grid_size(self) -> int:
        span = self.grid_note_max - self.grid_note_min + 1
        if span % 8 != 0:
            raise ValueError(f"{self.map_id}: grid span {span} is not a multiple of 8")
        return 8

    def matches_port(self, port_name: str) -> bool:
        lowered = port_name.casefold()
        return any(fragment in lowered for fragment in self.port_substrings)

    def matches_usb_id(self, usb_id: str) -> bool:
        normalized = usb_id.strip().casefold().replace(" ", "")
        return normalized in self.usb_ids

    def session_mode_sysex(self) -> bytes | None:
        return self.mode_sysex.get("session")

    def is_grid_note(self, note: int) -> bool:
        return self.grid_note_min <= note <= self.grid_note_max

    def is_scene_launch(self, note: int) -> bool:
        return note in self.scene_launch_notes

    def scene_launch_index(self, note: int) -> int | None:
        if note not in self.scene_launch_notes:
            return None
        return note - self.scene_launch_notes[0]

    def grid_position(self, note: int) -> tuple[int, int] | None:
        if not self.is_grid_note(note):
            return None
        size = self.grid_size
        offset = note - self.grid_note_min
        return divmod(offset, size)

    def grid_note(self, row: int, col: int) -> int:
        size = self.grid_size
        if not (0 <= row < size and 0 <= col < size):
            raise ValueError(f"grid row/col out of range: ({row}, {col})")
        return self.grid_note_min + row * size + col

    def looper_transport_action(
        self,
        note: int,
        mapping: dict[int, LooperTransportAction] | None = None,
    ) -> LooperTransportAction | None:
        table = self.looper_transport if mapping is None else mapping
        return table.get(note)
