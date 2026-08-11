"""MIDI control surface maps — registry and shared helpers."""

from patch_browser.control_surfaces.apc_mini import (
    APC_MAPS,
    APC_MAP_MK1,
    APC_MAP_MK2,
    ApcVariant,
    CONTROL_SURFACE_MAPS,
    default_apc_variant,
    get_apc_map,
    resolve_apc_map,
    variant_from_usb_id,
)
from patch_browser.control_surfaces.midi import (
    button_press,
    find_input_port_index,
    looper_transport_from_message,
    parse_channel_message,
)
from patch_browser.control_surfaces.types import ControlSurfaceMap, LooperTransportAction

__all__ = [
    "APC_MAPS",
    "APC_MAP_MK1",
    "APC_MAP_MK2",
    "ApcVariant",
    "CONTROL_SURFACE_MAPS",
    "ControlSurfaceMap",
    "LooperTransportAction",
    "button_press",
    "default_apc_variant",
    "find_input_port_index",
    "get_apc_map",
    "looper_transport_from_message",
    "parse_channel_message",
    "resolve_apc_map",
    "variant_from_usb_id",
]
