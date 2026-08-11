"""APC Session View MIDI — grid, scene launch, shift+stop-all, shift+hold clear."""

from __future__ import annotations

import time
from dataclasses import dataclass

from patch_browser.clip_matrix import ClipMatrix
from patch_browser.control_surfaces.midi import button_press, parse_channel_message
from patch_browser.control_surfaces.types import ControlSurfaceMap

CLEAR_SESSION_HOLD_S = 3.0


@dataclass
class ApcMidiContext:
    shift_held: bool = False
    stop_all_hold_started_at: float | None = None
    stop_all_clear_fired: bool = False


def _shift_pressed(surface: ControlSurfaceMap, note: int, is_on: bool, ctx: ApcMidiContext) -> bool:
    if surface.shift_note is None or note != surface.shift_note:
        return False
    ctx.shift_held = is_on
    return True


def check_clear_session_hold(
    ctx: ApcMidiContext,
    matrix: ClipMatrix,
    *,
    hold_s: float = CLEAR_SESSION_HOLD_S,
    now: float | None = None,
) -> bool:
    """Fire session clear after shift+Scene 8 held for ``hold_s`` seconds."""
    if ctx.stop_all_hold_started_at is None or ctx.stop_all_clear_fired:
        return False
    now = time.monotonic() if now is None else now
    if (now - ctx.stop_all_hold_started_at) < hold_s:
        return False
    ctx.stop_all_clear_fired = True
    matrix.clear_session()
    return True


def handle_apc_session_message(
    surface: ControlSurfaceMap,
    data: list[int] | tuple[int, ...],
    ctx: ApcMidiContext,
    matrix: ClipMatrix,
    *,
    now: float | None = None,
) -> str | None:
    """Handle one APC message; return a short log label or None."""
    now = time.monotonic() if now is None else now
    parsed = parse_channel_message(data)
    if parsed is None:
        return None
    channel, number, _value, is_on = parsed
    if channel != surface.midi_channel:
        return None

    if surface.shift_note is not None and number == surface.shift_note:
        ctx.shift_held = is_on
        if not is_on and ctx.stop_all_hold_started_at is not None:
            ctx.stop_all_hold_started_at = None
            ctx.stop_all_clear_fired = False
        return None

    if surface.is_scene_launch(number):
        index = surface.scene_launch_index(number)
        if index is None:
            return None
        if ctx.shift_held and index == 7:
            if is_on:
                ctx.stop_all_hold_started_at = now
                ctx.stop_all_clear_fired = False
                matrix.on_stop_all()
                return "stop_all (hold 3s to clear)"
            ctx.stop_all_hold_started_at = None
            ctx.stop_all_clear_fired = False
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
        matrix.on_scene(index)
        return f"scene {index + 1}"

    return None
