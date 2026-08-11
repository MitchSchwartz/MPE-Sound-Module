"""Shared MIDI parsing helpers for control surface maps."""

from __future__ import annotations

from patch_browser.control_surfaces.types import ControlSurfaceMap, LooperTransportAction


def parse_channel_message(data: list[int] | tuple[int, ...]) -> tuple[int, int, int, bool] | None:
    """Parse a 3-byte channel voice message → (channel, note_or_cc, value, is_on).

    Handles APC note-off-as-128-with-velocity-127 quirk on note buttons.
    """
    if len(data) < 3:
        return None
    status = data[0] & 0xF0
    channel = data[0] & 0x0F
    number = data[1]
    value = data[2]

    if status == 0x90:
        return channel, number, value, value > 0
    if status == 0x80:
        return channel, number, value, False
    if status == 0xB0:
        return channel, number, value, value > 0
    return None


def button_press(
    data: list[int] | tuple[int, ...],
    *,
    channel: int,
) -> tuple[int, int] | None:
    """Return (note, velocity) for a note-on button press, or None."""
    parsed = parse_channel_message(data)
    if parsed is None:
        return None
    msg_channel, note, velocity, is_on = parsed
    if msg_channel != channel or not is_on:
        return None
    return note, velocity


def looper_transport_from_message(
    surface: ControlSurfaceMap,
    data: list[int] | tuple[int, ...],
    *,
    mapping: dict[int, LooperTransportAction] | None = None,
) -> LooperTransportAction | None:
    """Map one inbound MIDI message to a looper transport action, if any."""
    press = button_press(data, channel=surface.midi_channel)
    if press is None:
        return None
    note, _velocity = press
    return surface.looper_transport_action(note, mapping=mapping)


def find_input_port_index(port_names: list[str], surface: ControlSurfaceMap) -> int | None:
    for index, name in enumerate(port_names):
        if surface.matches_port(name):
            return index
    return None
