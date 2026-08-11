"""Akai APC mini — backward-compatible facade over ``control_surfaces.apc_mini``.

Prefer importing from ``patch_browser.control_surfaces`` for new code.
"""

from __future__ import annotations

from patch_browser.control_surfaces.apc_mini import (
    APC_MAPS,
    APC_MAP_MK1,
    APC_MAP_MK2,
    APC_MIDI_CHANNEL,
    APC_USB_ID_MK1,
    APC_USB_ID_MK2,
    MK1_CLIP_STOP_GRID_NOTES,
    MK1_DEFAULT_LOOPER_TRANSPORT,
    MK1_SCENE_LAUNCH_LABELS,
    MK1_SCENE_LAUNCH_NOTES,
    MK1_SHIFT_NOTE,
    MK1_SOFT_KEY_NOTES,
    MK2_DEFAULT_LOOPER_TRANSPORT,
    MK2_MODE_SYSEX,
    MK2_SCENE_LAUNCH_DRUM_MODE,
    MK2_SCENE_LAUNCH_NOTE_MODE,
    MK2_SCENE_LAUNCH_NOTES,
    MK2_SHIFT_NOTE,
    MK2_TRACK_BUTTON_NOTES,
    ApcVariant,
    default_apc_variant,
    get_apc_map,
    resolve_apc_map,
    variant_from_usb_id,
)
from patch_browser.control_surfaces.midi import (
    button_press as is_apc_button_press,
    find_input_port_index,
    looper_transport_from_message as _looper_transport_for_surface,
    parse_channel_message,
)
from patch_browser.control_surfaces.types import LooperTransportAction

APC_PORT_SUBSTRINGS = APC_MAP_MK1.port_substrings
GRID_NOTE_MIN = APC_MAP_MK1.grid_note_min
GRID_NOTE_MAX = APC_MAP_MK1.grid_note_max
GRID_SIZE = APC_MAP_MK1.grid_size
FADER_CC_NUMBERS = APC_MAP_MK1.fader_cc_numbers
MASTER_FADER_CC = APC_MAP_MK1.master_fader_cc

# Back-compat aliases (early stub used mk2 note numbers).
TRACK_BUTTON_NOTES = MK2_TRACK_BUTTON_NOTES
SCENE_LAUNCH_NOTES = MK2_SCENE_LAUNCH_NOTES
SHIFT_NOTE = MK2_SHIFT_NOTE
DEFAULT_LOOPER_TRANSPORT = MK2_DEFAULT_LOOPER_TRANSPORT
SESSION_MODE_SYSEX = MK2_MODE_SYSEX.get("session")
NOTE_MODE_SYSEX = MK2_MODE_SYSEX.get("note")
DRUM_MODE_SYSEX = MK2_MODE_SYSEX.get("drum")


def scene_launch_notes(variant: ApcVariant) -> tuple[int, ...]:
    return get_apc_map(variant).scene_launch_notes


def shift_note(variant: ApcVariant) -> int | None:
    return get_apc_map(variant).shift_note


def default_looper_transport(variant: ApcVariant) -> dict[int, LooperTransportAction]:
    return get_apc_map(variant).looper_transport


def session_mode_sysex(variant: ApcVariant) -> bytes | None:
    return get_apc_map(variant).session_mode_sysex()


def is_apc_port_name(port_name: str) -> bool:
    return APC_MAP_MK1.matches_port(port_name)


def find_apc_input_port_index(port_names: list[str], *, variant: ApcVariant | None = None) -> int | None:
    return find_input_port_index(port_names, get_apc_map(variant))


def grid_note(row: int, col: int, *, variant: ApcVariant | None = None) -> int:
    return get_apc_map(variant).grid_note(row, col)


def grid_position(note: int, *, variant: ApcVariant | None = None) -> tuple[int, int] | None:
    return get_apc_map(variant).grid_position(note)


def is_grid_note(note: int, *, variant: ApcVariant | None = None) -> bool:
    return get_apc_map(variant).is_grid_note(note)


def is_scene_launch(note: int, *, variant: ApcVariant) -> bool:
    return get_apc_map(variant).is_scene_launch(note)


def scene_launch_index(note: int, *, variant: ApcVariant) -> int | None:
    return get_apc_map(variant).scene_launch_index(note)


def is_track_button(note: int, *, variant: ApcVariant = ApcVariant.MK2) -> bool:
    surface = get_apc_map(variant)
    if surface.track_button_notes:
        return note in surface.track_button_notes
    return note in surface.soft_key_notes


def looper_transport_action(
    note: int,
    mapping: dict[int, LooperTransportAction] | None = None,
    *,
    variant: ApcVariant | None = None,
) -> LooperTransportAction | None:
    return get_apc_map(variant).looper_transport_action(note, mapping=mapping)


def looper_transport_from_message(  # noqa: F811 — facade wrapper
    data: list[int] | tuple[int, ...],
    *,
    channel: int = APC_MIDI_CHANNEL,
    mapping: dict[int, LooperTransportAction] | None = None,
    variant: ApcVariant | None = None,
) -> LooperTransportAction | None:
    surface = get_apc_map(variant)
    if channel != surface.midi_channel:
        press = is_apc_button_press(data, channel=channel)
        if press is None:
            return None
        note, _ = press
        table = mapping if mapping is not None else surface.looper_transport
        return table.get(note)
    return _looper_transport_for_surface(surface, data, mapping=mapping)
