"""Akai APC mini mk1 + mk2 — registered control surface maps."""

from __future__ import annotations

import os
from enum import StrEnum

from patch_browser.control_surfaces.types import ControlSurfaceMap, LooperTransportAction

# Shared grid + faders (both revisions).
_APC_GRID_MIN = 0x00
_APC_GRID_MAX = 0x3F
_APC_FADER_CC = tuple(range(0x30, 0x38))
_APC_MASTER_FADER_CC = 0x38
APC_MIDI_CHANNEL = 0
_APC_PORT_SUBSTRINGS = ("apc mini", "apc_mini")

# USB hints only — env ``MPE_APC_VARIANT`` wins when set.
APC_USB_ID_MK1 = "09e8:0024"
APC_USB_ID_MK2 = "09e8:0028"

MK1_SCENE_LAUNCH_NOTES: tuple[int, ...] = tuple(range(0x52, 0x5A))  # 82–89
MK1_SHIFT_NOTE = 0x62
MK1_CLIP_STOP_GRID_NOTES: tuple[int, ...] = tuple(range(0x00, 0x08))
MK1_SOFT_KEY_NOTES: tuple[int, ...] = tuple(range(0x40, 0x48))
MK1_SCENE_LAUNCH_LABELS: tuple[str, ...] = (
    "clip_stop_mode",
    "solo_mode",
    "rec_arm_mode",
    "mute_mode",
    "select_mode",
    "scene_6",
    "scene_7",
    "stop_all_clips",
)

MK2_TRACK_BUTTON_NOTES: tuple[int, ...] = tuple(range(0x64, 0x6C))  # 100–107
MK2_SCENE_LAUNCH_NOTES: tuple[int, ...] = tuple(range(0x70, 0x78))  # 112–119
MK2_SHIFT_NOTE = 0x7A
MK2_SCENE_LAUNCH_DRUM_MODE = 0x75
MK2_SCENE_LAUNCH_NOTE_MODE = 0x76

_TRANSPORT_SCENE_INDICES = (0, 1, 4, 7)
_TRANSPORT_ACTIONS = (
    LooperTransportAction.RECORD,
    LooperTransportAction.OVERDUB,
    LooperTransportAction.PLAY_STOP,
    LooperTransportAction.CLEAR,
)


def _looper_transport_for_scene_notes(
    scene_notes: tuple[int, ...],
) -> dict[int, LooperTransportAction]:
    return {
        scene_notes[index]: action
        for index, action in zip(_TRANSPORT_SCENE_INDICES, _TRANSPORT_ACTIONS, strict=True)
    }


MK1_DEFAULT_LOOPER_TRANSPORT = _looper_transport_for_scene_notes(MK1_SCENE_LAUNCH_NOTES)
MK2_DEFAULT_LOOPER_TRANSPORT = _looper_transport_for_scene_notes(MK2_SCENE_LAUNCH_NOTES)

MK2_MODE_SYSEX: dict[str, bytes] = {
    "session": bytes.fromhex("F0 47 7F 4F 62 00 01 00 F7"),
    "note": bytes.fromhex("F0 47 7F 4F 62 00 01 01 F7"),
    "drum": bytes.fromhex("F0 47 7F 4F 62 00 01 02 F7"),
}


class ApcVariant(StrEnum):
    MK1 = "mk1"
    MK2 = "mk2"


APC_MAP_MK1 = ControlSurfaceMap(
    map_id="apc-mini-mk1",
    label="Akai APC mini (mk1)",
    port_substrings=_APC_PORT_SUBSTRINGS,
    usb_ids=(APC_USB_ID_MK1,),
    midi_channel=APC_MIDI_CHANNEL,
    scene_launch_notes=MK1_SCENE_LAUNCH_NOTES,
    shift_note=MK1_SHIFT_NOTE,
    grid_note_min=_APC_GRID_MIN,
    grid_note_max=_APC_GRID_MAX,
    looper_transport=MK1_DEFAULT_LOOPER_TRANSPORT,
    mode_sysex={},
    soft_key_notes=MK1_SOFT_KEY_NOTES,
    clip_stop_grid_notes=MK1_CLIP_STOP_GRID_NOTES,
    scene_launch_labels=MK1_SCENE_LAUNCH_LABELS,
    fader_cc_numbers=_APC_FADER_CC,
    master_fader_cc=_APC_MASTER_FADER_CC,
)

APC_MAP_MK2 = ControlSurfaceMap(
    map_id="apc-mini-mk2",
    label="Akai APC mini mk2",
    port_substrings=_APC_PORT_SUBSTRINGS,
    usb_ids=(APC_USB_ID_MK2,),
    midi_channel=APC_MIDI_CHANNEL,
    scene_launch_notes=MK2_SCENE_LAUNCH_NOTES,
    shift_note=MK2_SHIFT_NOTE,
    grid_note_min=_APC_GRID_MIN,
    grid_note_max=_APC_GRID_MAX,
    looper_transport=MK2_DEFAULT_LOOPER_TRANSPORT,
    mode_sysex=MK2_MODE_SYSEX,
    track_button_notes=MK2_TRACK_BUTTON_NOTES,
    scene_launch_labels=tuple(f"scene_{i + 1}" for i in range(8)),
    fader_cc_numbers=_APC_FADER_CC,
    master_fader_cc=_APC_MASTER_FADER_CC,
)

APC_MAPS: dict[ApcVariant, ControlSurfaceMap] = {
    ApcVariant.MK1: APC_MAP_MK1,
    ApcVariant.MK2: APC_MAP_MK2,
}

# Flat registry for cross-family lookup (``map_id`` → map).
CONTROL_SURFACE_MAPS: dict[str, ControlSurfaceMap] = {
    APC_MAP_MK1.map_id: APC_MAP_MK1,
    APC_MAP_MK2.map_id: APC_MAP_MK2,
}


def default_apc_variant() -> ApcVariant:
    raw = os.environ.get("MPE_APC_VARIANT", "mk1").strip().casefold()
    if raw in ("mk2", "2", "apc-mini-mk2", "apc mini mk2"):
        return ApcVariant.MK2
    return ApcVariant.MK1


def get_apc_map(variant: ApcVariant | None = None) -> ControlSurfaceMap:
    return APC_MAPS[variant or default_apc_variant()]


def resolve_apc_map(
    *,
    variant: ApcVariant | None = None,
    usb_id: str | None = None,
) -> ControlSurfaceMap:
    """Resolve map: explicit variant → env → USB hint → mk1 default."""
    if variant is not None:
        return get_apc_map(variant)
    env_variant = default_apc_variant()
    if os.environ.get("MPE_APC_VARIANT"):
        return get_apc_map(env_variant)
    if usb_id:
        for candidate in APC_MAPS.values():
            if candidate.matches_usb_id(usb_id):
                return candidate
    return get_apc_map(env_variant)


def variant_from_usb_id(usb_id: str) -> ApcVariant | None:
    normalized = usb_id.strip().casefold().replace(" ", "")
    for variant, surface in APC_MAPS.items():
        if normalized in surface.usb_ids:
            return variant
    return None
