"""Touch patch browser — mixer mixin."""

from __future__ import annotations

import time

from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.touch_ui_constants import (
    DEFAULT_VOLUME,
    FADER_HANDLE_H,
    MIXER_DOUBLE_TAP_MS,
    MIXER_DRAG_THRESHOLD_PX,
)


class TouchBrowserMixerMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _brightness_from_x(self, x: int, rect: Rect) -> int:
        if rect.w <= 0:
            return self.brightness_percent
        ratio = (x - rect.x) / rect.w
        return max(0, min(100, round(ratio * 100)))

    def _mixer_default_value(self, channel: MixerChannel) -> float:
        if channel.channel_id == "volume":
            return DEFAULT_VOLUME
        if channel.channel_id in ("tail", "touch"):
            return 0.0
        if channel.channel_id == "norm" and self.detail_patch:
            return self.loader.normalization.get_slider_default_gain_db(self.detail_patch["name"])
        return (channel.min_value + channel.max_value) / 2

    def _mixer_value(self, channel: MixerChannel) -> float:
        if channel.channel_id == "volume":
            return self.volume_level
        if channel.channel_id == "tail":
            return self._tail_offset_for_detail()
        if channel.channel_id == "touch":
            return self._touch_offset_for_detail()
        if channel.channel_id == "norm":
            return self._norm_gain_db_for_detail()
        return self._mixer_levels.get(channel.channel_id, self._mixer_default_value(channel))

    def _value_to_handle_y(self, channel: MixerChannel, value: float) -> int:
        span = channel.max_value - channel.min_value
        ratio = 0.0 if span <= 0 else (value - channel.min_value) / span
        ratio = max(0.0, min(1.0, ratio))
        travel = channel.track_rect.h - FADER_HANDLE_H
        return int(channel.track_rect.y + travel * (1.0 - ratio))

    def _value_from_track_y(self, channel: MixerChannel, y: int) -> float:
        travel = max(1, channel.track_rect.h - FADER_HANDLE_H)
        local = y - channel.track_rect.y - FADER_HANDLE_H // 2
        ratio = 1.0 - max(0.0, min(1.0, local / travel))
        return channel.min_value + ratio * (channel.max_value - channel.min_value)

    def _set_mixer_value(self, channel: MixerChannel, value: float, *, persist: bool = True) -> None:
        clamped = max(channel.min_value, min(channel.max_value, value))
        if channel.channel_id == "volume":
            if channel.enabled:
                self._apply_volume(clamped)
            return
        if channel.channel_id == "norm":
            if channel.enabled:
                self._apply_norm_gain_db(clamped, persist=persist)
            return
        if channel.channel_id == "tail":
            if channel.enabled:
                self._apply_tail_offset(clamped, persist=persist)
            return
        if channel.channel_id == "touch":
            if channel.enabled:
                self._apply_touch_offset(clamped, persist=persist)
            return
        self._mixer_levels[channel.channel_id] = clamped

    def _persist_mixer_drag(self) -> None:
        """Flush deferred norm/tail slider writes after finger-up."""
        channel_id = self._dragging_mixer_id
        if channel_id == "norm":
            self.loader.normalization.save()
        elif channel_id == "tail":
            self.loader.hold.save()
        elif channel_id == "touch":
            self.loader.pressure.save()

    def _reset_mixer_channel(self, channel: MixerChannel) -> None:
        if channel.channel_id == "norm":
            self._reset_norm_gain_to_calibrated()
            return
        if channel.channel_id == "tail":
            self._reset_tail_to_patch_default()
            return
        if channel.channel_id == "touch":
            self._reset_touch_to_default()
            return
        default = self._mixer_default_value(channel)
        self._set_mixer_value(channel, default)
        if channel.channel_id == "volume":
            self._toast("Volume reset", 1.2)
        elif channel.enabled:
            self._toast(f"{channel.label} reset", 1.2)

    def _mixer_channel_at(self, pos: tuple[int, int]) -> MixerChannel | None:
        for channel in self.mixer_channels:
            if channel.column_rect.contains(*pos):
                return channel
        return None

    def _handle_mixer_down(self, pos: tuple[int, int]) -> bool:
        channel = self._mixer_channel_at(pos)
        if channel is None:
            return False

        now = time.time()
        if (
            self._mixer_last_tap_id == channel.channel_id
            and not self._mixer_drag_moved
            and (now - self._mixer_last_tap_time) * 1000.0 <= MIXER_DOUBLE_TAP_MS
        ):
            self._reset_mixer_channel(channel)
            self._mixer_last_tap_id = None
            self._mixer_last_tap_time = 0.0
            self._mixer_drag_origin = None
            self._mixer_drag_moved = False
            self._dragging_mixer_id = None
            return True

        self._mixer_last_tap_id = channel.channel_id
        self._mixer_last_tap_time = now
        self._mixer_drag_origin = pos
        self._mixer_drag_moved = False
        self._dragging_mixer_id = channel.channel_id
        if channel.enabled:
            self._set_mixer_value(channel, self._value_from_track_y(channel, pos[1]), persist=False)
        return True

    def _handle_mixer_motion(self, pos: tuple[int, int]) -> None:
        if self._mixer_drag_origin and not self._mixer_drag_moved:
            dx = pos[0] - self._mixer_drag_origin[0]
            dy = pos[1] - self._mixer_drag_origin[1]
            if (dx * dx + dy * dy) ** 0.5 > MIXER_DRAG_THRESHOLD_PX:
                self._mixer_drag_moved = True
                self._mixer_last_tap_id = None
        if not self._dragging_mixer_id:
            return
        for channel in self.mixer_channels:
            if channel.channel_id == self._dragging_mixer_id and channel.enabled:
                self._set_mixer_value(
                    channel,
                    self._value_from_track_y(channel, pos[1]),
                    persist=False,
                )
                break
