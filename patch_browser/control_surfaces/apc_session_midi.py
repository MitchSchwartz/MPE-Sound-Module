"""APC Session View MIDI — grid, scene launch, shift+stop-all."""

from __future__ import annotations

from dataclasses import dataclass

from patch_browser.clip_matrix import ClipMatrix
from patch_browser.control_surfaces.midi import button_press, parse_channel_message
from patch_browser.control_surfaces.types import ControlSurfaceMap


@dataclass
class ApcMidiContext:
    shift_held: bool = False


def _shift_pressed(surface: ControlSurfaceMap, note: int, is_on: bool, ctx: ApcMidiContext) -> bool:
    if surface.shift_note is None or note != surface.shift_note:
        return False
    ctx.shift_held = is_on
    return True


def handle_apc_session_message(
    surface: ControlSurfaceMap,
    data: list[int] | tuple[int, ...],
    ctx: ApcMidiContext,
    matrix: ClipMatrix,
) -> str | None:
    """Handle one APC message; return a short log label or None."""
    parsed = parse_channel_message(data)
    if parsed is None:
        return None
    channel, number, _value, is_on = parsed
    if channel != surface.midi_channel:
        return None

    if surface.shift_note is not None and number == surface.shift_note:
        ctx.shift_held = is_on
        return None

    press = button_press(data, channel=surface.midi_channel)
    if press is None:
        return None
    note, _velocity = press

    if surface.is_grid_note(note):
        pos = surface.grid_position(note)
        if pos is None:
            return None
        row, col = pos
        matrix.on_grid(row, col)
        return f"grid ({row},{col})"

    if surface.is_scene_launch(note):
        index = surface.scene_launch_index(note)
        if index is None:
            return None
        if ctx.shift_held and index == 7:
            matrix.on_stop_all()
            return "stop_all"
        matrix.on_scene(index)
        return f"scene {index + 1}"

    return None
