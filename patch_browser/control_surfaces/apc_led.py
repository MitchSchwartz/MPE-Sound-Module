"""APC mini mk1 LED feedback via standard note-on velocities.

Protocol matches Akai docs and community references (asus4/setup_apcmini.py, launchpy).
mk2 RGB uses a different channel nibble — only mk1 velocities here for v0.
"""

from __future__ import annotations

import time
from enum import IntEnum

from patch_browser.control_surfaces.types import ControlSurfaceMap


class ApcLedColor(IntEnum):
    OFF = 0
    GREEN = 1
    GREEN_BLINK = 2
    RED = 3
    RED_BLINK = 4
    YELLOW = 5
    YELLOW_BLINK = 6


def led_note_on_bytes(*, note: int, color: ApcLedColor, channel: int = 0) -> list[int]:
    return [0x90 | (channel & 0x0F), note & 0x7F, int(color)]


def transport_led_notes(surface: ControlSurfaceMap) -> dict[str, int | None]:
    """Scene slots used for looper transport (indices 0, 1, 4, 7)."""
    scenes = surface.scene_launch_notes
    return {
        "record": scenes[0] if len(scenes) > 0 else None,
        "overdub": scenes[1] if len(scenes) > 1 else None,
        "play_stop": scenes[4] if len(scenes) > 4 else None,
        "clear": scenes[7] if len(scenes) > 7 else None,
    }


class ApcLedFeedback:
    """Rate-limited LED writer for one APC map (mk1 velocity colors)."""

    MIN_SEND_INTERVAL_S = 0.0015

    def __init__(self, midi_out, surface: ControlSurfaceMap) -> None:
        self._out = midi_out
        self._surface = surface
        self._channel = surface.midi_channel
        self._slots = transport_led_notes(surface)
        self._last_send = 0.0
        self._grid_led_cache: dict[int, ApcLedColor] = {}

    def _send(self, message: list[int]) -> None:
        now = time.monotonic()
        elapsed = now - self._last_send
        if elapsed < self.MIN_SEND_INTERVAL_S:
            time.sleep(self.MIN_SEND_INTERVAL_S - elapsed)
        self._out.send_message(message)
        self._last_send = time.monotonic()

    def set_note(self, note: int, color: ApcLedColor, *, rate_limit: bool = True) -> None:
        if rate_limit:
            self._send(led_note_on_bytes(note=note, color=color, channel=self._channel))
        else:
            self._out.send_message(led_note_on_bytes(note=note, color=color, channel=self._channel))

    def all_off(self) -> None:
        for note in range(100):
            self.set_note(note, ApcLedColor.OFF, rate_limit=True)

    def show_looper_state(self, *, recording: bool, playing: bool, has_loop: bool) -> None:
        record_note = self._slots.get("record")
        play_note = self._slots.get("play_stop")
        clear_note = self._slots.get("clear")
        if record_note is not None:
            if recording:
                self.set_note(record_note, ApcLedColor.RED_BLINK)
            else:
                self.set_note(record_note, ApcLedColor.OFF)
        if play_note is not None:
            if playing and has_loop:
                self.set_note(play_note, ApcLedColor.GREEN)
            else:
                self.set_note(play_note, ApcLedColor.OFF)
        if clear_note is not None:
            self.set_note(
                clear_note,
                ApcLedColor.YELLOW if has_loop else ApcLedColor.OFF,
            )

    def show_clip_matrix(self, matrix, *, surface: ControlSurfaceMap | None = None) -> None:
        """Update grid pad LEDs from clip matrix slot states (enabled slots only).

        Only sends MIDI when a pad color changes — never sleeps in the audio loop.
        """
        surf = surface or self._surface
        from patch_browser.clip_matrix import ClipState

        color_map = {
            ClipState.EMPTY: ApcLedColor.OFF,
            ClipState.RECORDING: ApcLedColor.RED_BLINK,
            ClipState.STOPPED: ApcLedColor.YELLOW,
            ClipState.PLAYING: ApcLedColor.GREEN,
            ClipState.STOPPING: ApcLedColor.YELLOW_BLINK,
        }
        for key in matrix.enabled_slots:
            note = surf.grid_note(key[0], key[1])
            clip = matrix.slots.get(key)
            state = clip.state if clip is not None else ClipState.EMPTY
            color = color_map.get(state, ApcLedColor.OFF)
            if self._grid_led_cache.get(note) == color:
                continue
            self._grid_led_cache[note] = color
            self._out.send_message(
                led_note_on_bytes(note=note, color=color, channel=self._channel)
            )

    def clear_grid_led_cache(self) -> None:
        self._grid_led_cache.clear()
