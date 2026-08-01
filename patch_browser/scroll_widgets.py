"""Touch-scrollable list and content scroll widgets."""

from __future__ import annotations

import math
import time

import pygame

from patch_browser.geometry import Rect
from patch_browser.touch_ui_constants import (
    SCROLL_DRAG_THRESHOLD_CATCH_PX,
    SCROLL_DRAG_THRESHOLD_PX,
    SCROLL_FRICTION,
    SCROLL_MIN_VELOCITY,
    SCROLL_SAMPLE_WINDOW_S,
    SCROLL_VELOCITY_CAP,
    SCROLL_VELOCITY_DRAG_PX_S,
)
from patch_browser.ui_text import ellipsize_text
from patch_browser.ui_theme import Theme


class ScrollList:
    """Touch-scrollable list with tap vs scroll discrimination and inertial momentum."""

    def __init__(self, rect: Rect, row_height: int = 56, padding: int = 8):
        self.rect = rect
        self.row_height = row_height
        self.padding = padding
        self.items: list[str] = []
        self.highlight_index: int | None = None
        self.loaded_marker_index: int | None = None
        self.scroll_offset = 0
        self._scroll_pixels = 0.0
        self._drag_start_y: int | None = None
        self._drag_scroll_pixels_start = 0.0
        self._pointer_down_pos: tuple[int, int] | None = None
        self._pointer_scrolled = False
        self._velocity = 0.0
        self._momentum_active = False
        self._last_motion_y: int | None = None
        self._last_motion_time = 0.0
        self._drag_start_time = 0.0
        self._scroll_samples: list[tuple[float, float]] = []
        self._pending_tap_index: int | None = None
        self._was_momentum_on_down = False

    def take_tap_index(self) -> int | None:
        idx = self._pending_tap_index
        self._pending_tap_index = None
        return idx

    def is_interacting(self) -> bool:
        return self._drag_start_y is not None or self._momentum_active

    def is_dragging(self) -> bool:
        return self._drag_start_y is not None

    def pointer_down(self, pos: tuple[int, int]) -> bool:
        """Begin tracking if touch is inside the list."""
        if not self.rect.contains(*pos):
            return False
        was_momentum = self._momentum_active
        self.stop_momentum()
        self._clear_pointer()
        self._was_momentum_on_down = was_momentum
        self._pointer_down_pos = pos
        self._drag_start_y = pos[1]
        self._drag_scroll_pixels_start = self._scroll_pixels
        self._last_motion_y = pos[1]
        now = time.time()
        self._last_motion_time = now
        self._drag_start_time = now
        self._scroll_samples = [(now, self._scroll_pixels)]
        return True

    def pointer_move(self, pos: tuple[int, int]) -> bool:
        if self._drag_start_y is None:
            return False
        if not self._pointer_scrolled:
            move = self._pointer_move_distance(pos)
            threshold = (
                SCROLL_DRAG_THRESHOLD_CATCH_PX
                if self._was_momentum_on_down
                else SCROLL_DRAG_THRESHOLD_PX
            )
            now = time.time()
            velocity_bypass = False
            if self._last_motion_y is not None:
                motion_dt = now - self._last_motion_time
                if motion_dt > 0:
                    instant_v = abs(pos[1] - self._last_motion_y) / motion_dt
                    if instant_v >= SCROLL_VELOCITY_DRAG_PX_S:
                        velocity_bypass = True
            down_dt = now - self._drag_start_time
            if down_dt > 0.008:
                avg_v = abs(pos[1] - self._drag_start_y) / down_dt
                if avg_v >= SCROLL_VELOCITY_DRAG_PX_S:
                    velocity_bypass = True
            if move <= threshold and not velocity_bypass:
                return True
            self._pointer_scrolled = True

        delta = pos[1] - self._drag_start_y
        self._scroll_pixels = self._drag_scroll_pixels_start - float(delta)
        self._clamp_scroll()
        self._record_scroll_sample()

        now = time.time()
        if self._last_motion_y is not None:
            motion_dt = now - self._last_motion_time
            if motion_dt > 0:
                instant_v = -(pos[1] - self._last_motion_y) / motion_dt
                self._velocity = max(
                    -SCROLL_VELOCITY_CAP,
                    min(
                        SCROLL_VELOCITY_CAP,
                        self._velocity * 0.55 + instant_v * 0.45,
                    ),
                )
        self._last_motion_y = pos[1]
        self._last_motion_time = now
        return True

    def pointer_up(self, pos: tuple[int, int]) -> int | None:
        """End gesture; return tapped row index or None if scroll/miss."""
        self._pending_tap_index = None
        if self._drag_start_y is None and self._pointer_down_pos is None:
            return None

        if self._drag_start_y is not None:
            release_v = self._release_velocity()
            if self._pointer_scrolled and abs(release_v) >= SCROLL_MIN_VELOCITY:
                self._velocity = release_v
                self._momentum_active = True
            else:
                self.stop_momentum()
            self._drag_start_y = None
            self._last_motion_y = None

        if self._momentum_active:
            self._clear_pointer()
            return None
        if self._pointer_down_pos is None:
            return None
        if self._pointer_scrolled:
            self._clear_pointer()
            return None
        if not self.rect.contains(*self._pointer_down_pos):
            self._clear_pointer()
            return None
        index = self.item_at(*self._pointer_down_pos)
        self._pending_tap_index = index
        self._clear_pointer()
        return index

    def set_items(
        self,
        items: list[str],
        highlight_index: int | None = None,
        loaded_marker_index: int | None = None,
        *,
        preserve_scroll: bool = True,
    ) -> None:
        self.items = items
        self.highlight_index = highlight_index
        self.loaded_marker_index = loaded_marker_index
        if preserve_scroll:
            self._scroll_pixels = max(0.0, min(self._scroll_pixels, self._max_scroll_pixels()))
            self._sync_scroll_offset()
        else:
            self._scroll_pixels = 0.0
            self.stop_momentum()
            self._sync_scroll_offset()

    def stop_momentum(self) -> None:
        self._velocity = 0.0
        self._momentum_active = False

    def _pointer_move_distance(self, pos: tuple[int, int]) -> float:
        if self._pointer_down_pos is None:
            return 0.0
        dx = pos[0] - self._pointer_down_pos[0]
        dy = pos[1] - self._pointer_down_pos[1]
        return (dx * dx + dy * dy) ** 0.5

    def _clear_pointer(self) -> None:
        self._pointer_down_pos = None
        self._pointer_scrolled = False
        self._drag_start_y = None
        self._last_motion_y = None
        self._was_momentum_on_down = False
        self._scroll_samples.clear()

    def _record_scroll_sample(self) -> None:
        now = time.time()
        self._scroll_samples.append((now, self._scroll_pixels))
        cutoff = now - SCROLL_SAMPLE_WINDOW_S
        self._scroll_samples = [(t, s) for t, s in self._scroll_samples if t >= cutoff]

    def _release_velocity(self) -> float:
        now = time.time()
        self._record_scroll_sample()
        if len(self._scroll_samples) >= 2:
            t0, s0 = self._scroll_samples[0]
            t1, s1 = self._scroll_samples[-1]
            dt = t1 - t0
            if dt > 0.008:
                return max(
                    -SCROLL_VELOCITY_CAP,
                    min(SCROLL_VELOCITY_CAP, (s1 - s0) / dt),
                )
        return self._velocity

    def tick(self, dt: float) -> bool:
        """Advance inertial scroll. Returns True if scroll position changed."""
        if not self._momentum_active:
            return False
        dt = max(dt, 1.0 / 120.0)

        before = self._scroll_pixels
        self._scroll_pixels += self._velocity * dt
        self._clamp_scroll()

        if self._scroll_pixels != before and (
            self._scroll_pixels <= 0.0 or self._scroll_pixels >= self._max_scroll_pixels()
        ):
            self._velocity *= 0.35

        self._velocity *= math.exp(-SCROLL_FRICTION * dt)
        if abs(self._velocity) < SCROLL_MIN_VELOCITY:
            self.stop_momentum()
        return self._scroll_pixels != before or self._momentum_active

    def visible_count(self) -> int:
        inner_h = self.rect.h - self.padding * 2
        return max(1, inner_h // self.row_height)

    def _max_scroll(self) -> int:
        return max(0, len(self.items) - self.visible_count())

    def _max_scroll_pixels(self) -> float:
        return float(self._max_scroll() * self.row_height)

    def _sync_scroll_offset(self) -> None:
        maximum = self._max_scroll()
        row = int(self._scroll_pixels // self.row_height)
        self.scroll_offset = max(0, min(maximum, row))

    def _clamp_scroll(self) -> None:
        max_pixels = self._max_scroll_pixels()
        self._scroll_pixels = max(0.0, min(self._scroll_pixels, max_pixels))
        self._sync_scroll_offset()

    def item_at(self, px: int, py: int) -> int | None:
        if not self.rect.contains(px, py) or not self.items:
            return None
        local_y = py - self.rect.y - self.padding + self._scroll_pixels
        index = int(local_y // self.row_height)
        if 0 <= index < len(self.items):
            return index
        return None

    def scroll_to_index(self, index: int) -> None:
        if not self.items:
            return
        index = max(0, min(index, len(self.items) - 1))
        visible = self.visible_count()
        if index < self.scroll_offset:
            self.scroll_offset = index
        elif index >= self.scroll_offset + visible:
            self.scroll_offset = index - visible + 1
        self._scroll_pixels = float(self.scroll_offset * self.row_height)
        self.stop_momentum()
        self._clamp_scroll()

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self.pointer_down(event.pos)
        if event.type == pygame.MOUSEMOTION:
            return self.pointer_move(event.pos)
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._drag_start_y is None and self._pointer_down_pos is None:
                return False
            self.pointer_up(event.pos)
            return True
        if event.type == pygame.MOUSEWHEEL:
            if self.rect.contains(*pygame.mouse.get_pos()):
                self.stop_momentum()
                self._scroll_pixels -= event.y * self.row_height
                self._clamp_scroll()
                return True
        return False

    def draw(self, surface: pygame.Surface, font: pygame.font.Font, theme: Theme) -> None:
        pygame.draw.rect(surface, theme.surface, self.rect.pygame_rect, border_radius=10)
        clip = surface.get_clip()
        surface.set_clip(self.rect.pygame_rect)

        if not self.items:
            surface.set_clip(clip)
            return

        start = int(self._scroll_pixels // self.row_height)
        sub_pixel = self._scroll_pixels - start * self.row_height
        end = min(len(self.items), start + self.visible_count() + 3)
        y = self.rect.y + self.padding - int(sub_pixel)

        for index in range(start, end):
            label = self.items[index]
            row_rect = pygame.Rect(self.rect.x + 4, y, self.rect.w - 8, self.row_height - 4)
            is_highlight = self.highlight_index == index
            is_loaded = self.loaded_marker_index == index
            if is_highlight or is_loaded:
                pygame.draw.rect(surface, theme.surface_alt, row_rect, border_radius=8)

            text_color = theme.text if is_highlight or is_loaded else theme.muted
            max_w = max(1, row_rect.w - 28)
            clipped = ellipsize_text(font, label, max_w)
            text = font.render(clipped, True, text_color)
            ty = row_rect.y + (row_rect.h - text.get_height()) // 2
            surface.blit(text, (row_rect.x + 10, ty))

            if is_loaded:
                pygame.draw.circle(surface, theme.accent, (row_rect.right - 16, row_rect.centery), 5)

            y += self.row_height

        surface.set_clip(clip)


class ContentScrollArea:
    """Pixel-based vertical scroll for panels taller than their viewport."""

    def __init__(self, viewport: Rect):
        self.viewport = viewport
        self.content_height = 0
        self._scroll_pixels = 0.0
        self._drag_start_y: int | None = None
        self._drag_scroll_start = 0.0
        self._pointer_down_pos: tuple[int, int] | None = None
        self._pointer_scrolled = False
        self._velocity = 0.0
        self._momentum_active = False
        self._scroll_samples: list[tuple[float, float]] = []

    @property
    def scroll_pixels(self) -> float:
        return self._scroll_pixels

    def reset(self) -> None:
        self._scroll_pixels = 0.0
        self.stop_momentum()
        self._clear_pointer()

    def stop_momentum(self) -> None:
        self._velocity = 0.0
        self._momentum_active = False

    def _max_scroll_pixels(self) -> float:
        return max(0.0, float(self.content_height - self.viewport.h))

    def _clamp_scroll(self) -> None:
        self._scroll_pixels = max(0.0, min(self._scroll_pixels, self._max_scroll_pixels()))

    def _clear_pointer(self) -> None:
        self._pointer_down_pos = None
        self._pointer_scrolled = False
        self._drag_start_y = None
        self._scroll_samples.clear()

    def is_interacting(self) -> bool:
        return self._drag_start_y is not None or self._momentum_active

    def pointer_down(self, pos: tuple[int, int]) -> bool:
        if not self.viewport.contains(*pos):
            return False
        self.stop_momentum()
        self._clear_pointer()
        self._pointer_down_pos = pos
        self._drag_start_y = pos[1]
        self._drag_scroll_start = self._scroll_pixels
        self._scroll_samples = [(time.time(), self._scroll_pixels)]
        return True

    def pointer_move(self, pos: tuple[int, int]) -> bool:
        if self._drag_start_y is None:
            return False
        if not self._pointer_scrolled:
            dx = pos[0] - self._pointer_down_pos[0]
            dy = pos[1] - self._pointer_down_pos[1]
            if (dx * dx + dy * dy) ** 0.5 >= SCROLL_DRAG_THRESHOLD_PX:
                self._pointer_scrolled = True
        if self._pointer_scrolled:
            self._scroll_pixels = self._drag_scroll_start - (pos[1] - self._drag_start_y)
            self._clamp_scroll()
            self._scroll_samples.append((time.time(), self._scroll_pixels))
            cutoff = time.time() - SCROLL_SAMPLE_WINDOW_S
            self._scroll_samples = [(t, s) for t, s in self._scroll_samples if t >= cutoff]
            return True
        return False

    def pointer_up(self, pos: tuple[int, int]) -> bool:
        scrolled = self._pointer_scrolled
        if scrolled and len(self._scroll_samples) >= 2:
            t0, s0 = self._scroll_samples[0]
            t1, s1 = self._scroll_samples[-1]
            dt = t1 - t0
            if dt > 0.008:
                self._velocity = max(
                    -SCROLL_VELOCITY_CAP,
                    min(SCROLL_VELOCITY_CAP, (s1 - s0) / dt),
                )
                if abs(self._velocity) >= SCROLL_MIN_VELOCITY:
                    self._momentum_active = True
        self._clear_pointer()
        return scrolled

    def tick(self, dt: float) -> bool:
        if not self._momentum_active:
            return False
        dt = max(dt, 1.0 / 120.0)
        before = self._scroll_pixels
        self._scroll_pixels += self._velocity * dt
        self._clamp_scroll()
        if self._scroll_pixels != before and (
            self._scroll_pixels <= 0.0 or self._scroll_pixels >= self._max_scroll_pixels()
        ):
            self._velocity *= 0.35
        self._velocity *= math.exp(-SCROLL_FRICTION * dt)
        if abs(self._velocity) < SCROLL_MIN_VELOCITY:
            self.stop_momentum()
        return self._scroll_pixels != before or self._momentum_active
