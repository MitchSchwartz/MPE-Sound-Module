"""Touch-scrollable list and content scroll widgets."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

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


def compute_release_velocity(
    samples: list[tuple[float, float]],
    *,
    cap: float = SCROLL_VELOCITY_CAP,
    min_dt: float = 0.008,
) -> float:
    """Release speed (px/s) from scroll-position samples — shared by all scroll widgets."""
    if len(samples) < 2:
        return 0.0
    t0, s0 = samples[0]
    t1, s1 = samples[-1]
    dt = t1 - t0
    if dt <= min_dt:
        return 0.0
    return max(-cap, min(cap, (s1 - s0) / dt))


class ScrollList:
    """Touch-scrollable list with tap vs scroll discrimination and inertial momentum."""

    def __init__(
        self,
        rect: Rect,
        row_height: int = 56,
        padding: int = 8,
        *,
        drag_threshold_px: float = SCROLL_DRAG_THRESHOLD_PX,
        friction: float = SCROLL_FRICTION,
        min_velocity: float = SCROLL_MIN_VELOCITY,
        velocity_cap: float = SCROLL_VELOCITY_CAP,
    ):
        self.rect = rect
        self.row_height = row_height
        self.padding = padding
        self._drag_threshold_px = drag_threshold_px
        self._friction = friction
        self._min_velocity = min_velocity
        self._velocity_cap = velocity_cap
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
        self._drag_start_time = 0.0
        self._scroll_samples: list[tuple[float, float]] = []
        self._pending_tap_index: int | None = None
        self._was_momentum_on_down = False
        self._scroll_anim_from = 0.0
        self._scroll_anim_target: float | None = None
        self._scroll_anim_elapsed = 0.0
        self._scroll_anim_duration = 0.0
        self.pressed_index: int | None = None
        self.row_touch_feedback: Callable[[int], tuple[bool, float]] | None = None

    def cancel_active_pointer(self) -> None:
        """Drop in-progress tap/scroll gesture without firing a row tap."""
        self._pending_tap_index = None
        self._clear_pointer()

    def take_tap_index(self) -> int | None:
        idx = self._pending_tap_index
        self._pending_tap_index = None
        return idx

    def is_interacting(self) -> bool:
        return (
            self._drag_start_y is not None
            or self._momentum_active
            or self._scroll_anim_target is not None
        )

    def is_scroll_animating(self) -> bool:
        return self._scroll_anim_target is not None

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
        now = time.time()
        self._drag_start_time = now
        self._scroll_samples = [(now, self._scroll_pixels)]
        self.pressed_index = self.item_at(pos[0], pos[1])
        return True

    def pointer_move(self, pos: tuple[int, int]) -> bool:
        if self._drag_start_y is None:
            return False
        if not self._pointer_scrolled:
            move = self._pointer_move_distance(pos)
            threshold = (
                SCROLL_DRAG_THRESHOLD_CATCH_PX
                if self._was_momentum_on_down
                else self._drag_threshold_px
            )
            now = time.time()
            velocity_bypass = False
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
        return True

    def pointer_up(self, pos: tuple[int, int]) -> int | None:
        """End gesture; return tapped row index or None if scroll/miss."""
        self._pending_tap_index = None
        if self._drag_start_y is None and self._pointer_down_pos is None:
            return None

        if self._drag_start_y is not None:
            release_v = compute_release_velocity(
                self._scroll_samples,
                cap=self._velocity_cap,
            )
            if self._pointer_scrolled and abs(release_v) >= self._min_velocity:
                self._velocity = release_v
                self._momentum_active = True
            else:
                self.stop_momentum()
            self._drag_start_y = None

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
        self._scroll_anim_target = None

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
        self.pressed_index = None
        self._was_momentum_on_down = False
        self._scroll_samples.clear()

    def _record_scroll_sample(self) -> None:
        now = time.time()
        self._scroll_samples.append((now, self._scroll_pixels))
        cutoff = now - SCROLL_SAMPLE_WINDOW_S
        self._scroll_samples = [(t, s) for t, s in self._scroll_samples if t >= cutoff]

    def tick(self, dt: float) -> bool:
        """Advance inertial scroll / animated jumps. Returns True if scroll position changed."""
        dt = max(dt, 1.0 / 120.0)
        if self._scroll_anim_target is not None:
            before = self._scroll_pixels
            self._scroll_anim_elapsed += dt
            progress = min(
                1.0,
                self._scroll_anim_elapsed / max(self._scroll_anim_duration, 1e-6),
            )
            eased = 1.0 - (1.0 - progress) ** 3
            self._scroll_pixels = (
                self._scroll_anim_from
                + (self._scroll_anim_target - self._scroll_anim_from) * eased
            )
            self._clamp_scroll()
            if progress >= 1.0:
                self._scroll_anim_target = None
            return self._scroll_pixels != before

        if not self._momentum_active:
            return False

        before = self._scroll_pixels
        self._scroll_pixels += self._velocity * dt
        self._clamp_scroll()

        if self._scroll_pixels != before and (
            self._scroll_pixels <= 0.0 or self._scroll_pixels >= self._max_scroll_pixels()
        ):
            self._velocity *= 0.35

        self._velocity *= math.exp(-self._friction * dt)
        if abs(self._velocity) < self._min_velocity:
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

    def scroll_to_index(self, index: int, *, align: str = "visible") -> None:
        if not self.items:
            return
        index = max(0, min(index, len(self.items) - 1))
        if align == "top":
            self.scroll_offset = index
        else:
            visible = self.visible_count()
            if index < self.scroll_offset:
                self.scroll_offset = index
            elif index >= self.scroll_offset + visible:
                self.scroll_offset = index - visible + 1
        self._scroll_pixels = float(self.scroll_offset * self.row_height)
        self.stop_momentum()
        self._clamp_scroll()

    def animate_scroll_to_index(
        self,
        index: int,
        *,
        align: str = "top",
        duration: float = 0.22,
    ) -> None:
        if not self.items:
            return
        index = max(0, min(index, len(self.items) - 1))
        if align == "top":
            target_pixels = float(index * self.row_height)
        else:
            self.scroll_to_index(index, align="visible")
            return
        self.stop_momentum()
        self._scroll_anim_from = self._scroll_pixels
        self._scroll_anim_target = target_pixels
        self._scroll_anim_elapsed = 0.0
        self._scroll_anim_duration = max(0.05, duration)

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
            if self.row_touch_feedback:
                pressed, progress = self.row_touch_feedback(index)
                if pressed:
                    pygame.draw.rect(surface, theme.surface_alt, row_rect, border_radius=8)
                    if progress > 0:
                        bar_h = 3
                        bar_w = max(4, int(row_rect.w * progress))
                        pygame.draw.rect(
                            surface,
                            theme.accent,
                            (row_rect.x, row_rect.bottom - bar_h - 2, bar_w, bar_h),
                            border_radius=2,
                        )
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
        self._hint_top = 0.0
        self._hint_bottom = 0.0

    @property
    def scroll_pixels(self) -> float:
        return self._scroll_pixels

    def reset(self) -> None:
        self._scroll_pixels = 0.0
        self.stop_momentum()
        self._clear_pointer()
        self._hint_top = 0.0
        self._hint_bottom = 0.0

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

    @property
    def scroll_gesture_active(self) -> bool:
        return self._pointer_scrolled

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
        if scrolled:
            release_v = compute_release_velocity(self._scroll_samples)
            if abs(release_v) >= SCROLL_MIN_VELOCITY:
                self._velocity = release_v
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

    def is_scrollable(self) -> bool:
        return self._max_scroll_pixels() > 0.5

    def can_scroll_up(self) -> bool:
        return self._scroll_pixels > 2.0

    def can_scroll_down(self) -> bool:
        return self._scroll_pixels < self._max_scroll_pixels() - 2.0

    HINT_FADE_IN_RATE = 2.8
    HINT_FADE_OUT_RATE = 3.5

    def _ease_hint_strength(self, current: float, target: float, dt: float) -> float:
        if abs(target - current) < 0.008:
            return target
        rate = self.HINT_FADE_IN_RATE if target > current else self.HINT_FADE_OUT_RATE
        return current + (target - current) * min(1.0, rate * dt)

    def tick_edge_hints(self, dt: float) -> None:
        """Smoothly animate overflow hint visibility."""
        dt = max(dt, 1.0 / 120.0)
        if not self.is_scrollable():
            top_target = 0.0
            bottom_target = 0.0
        else:
            top_target = 1.0 if self.can_scroll_up() else 0.0
            bottom_target = 1.0 if self.can_scroll_down() else 0.0
        self._hint_top = self._ease_hint_strength(self._hint_top, top_target, dt)
        self._hint_bottom = self._ease_hint_strength(self._hint_bottom, bottom_target, dt)

    def edge_hint_strength(self, edge: str) -> float:
        raw = self._hint_bottom if edge == "bottom" else self._hint_top
        raw = max(0.0, min(1.0, raw))
        # Smoothstep so hints ease in/out visually instead of popping linearly.
        return raw * raw * (3.0 - 2.0 * raw)


def _scroll_hint_style(
    theme: Theme,
    fade_rgb: tuple[int, int, int] | None,
) -> tuple[tuple[int, int, int], int, float, float, bool]:
    """Return fade color, band height, alpha curve, max opacity, chevron flag."""
    if theme.surface_elevated is not None:
        return (
            fade_rgb or theme.panel_surface(),
            18,
            1.25,
            0.14,
            True,
        )
    return fade_rgb or theme.panel_surface(), 16, 1.0, 0.28, False


def _edge_fade_alpha(row: int, fade_h: int, *, edge: str, alpha_power: float) -> int:
    if edge == "bottom":
        t = (row + 1) / fade_h
    else:
        t = (fade_h - row) / fade_h
    return int(255 * (t**alpha_power))


def _draw_vertical_edge_fade(
    surface: pygame.Surface,
    viewport: Rect,
    rgb: tuple[int, int, int],
    *,
    edge: str,
    fade_h: int,
    alpha_power: float = 1.0,
    strength: float = 1.0,
    max_opacity: float = 1.0,
) -> None:
    if strength <= 0.008:
        return
    fade_h = max(4, min(fade_h, max(4, viewport.h // 3)))
    band = pygame.Surface((viewport.w, fade_h), pygame.SRCALPHA)
    cap = max(0.0, min(1.0, max_opacity)) * max(0.0, min(1.0, strength))
    for row in range(fade_h):
        alpha = int(_edge_fade_alpha(row, fade_h, edge=edge, alpha_power=alpha_power) * cap)
        if alpha <= 0:
            continue
        pygame.draw.line(band, (*rgb, alpha), (0, row), (viewport.w, row))
    y = viewport.bottom - fade_h if edge == "bottom" else viewport.y
    surface.blit(band, (viewport.x, y))


def _draw_scroll_edge_chevron(
    surface: pygame.Surface,
    viewport: Rect,
    color: tuple[int, int, int],
    *,
    edge: str,
    strength: float = 1.0,
) -> None:
    if strength <= 0.008:
        return
    slide = int((1.0 - strength) * 5)
    cx = viewport.centerx
    w, h = 18, 12
    patch = pygame.Surface((w, h), pygame.SRCALPHA)
    pcx = w // 2
    if edge == "bottom":
        points = [(pcx - 7, 2), (pcx + 7, 2), (pcx, 9)]
        blit_y = viewport.bottom - 11 + slide - 5
    else:
        points = [(pcx - 7, h - 3), (pcx + 7, h - 3), (pcx, 0)]
        blit_y = viewport.y + 11 - slide - 5
    alpha = int(255 * max(0.0, min(1.0, strength)))
    pygame.draw.polygon(patch, (*color, alpha), points)
    surface.blit(patch, (cx - w // 2, blit_y))


def draw_vertical_scroll_edge_hints(
    surface: pygame.Surface,
    viewport: Rect,
    scroll: ContentScrollArea,
    theme: Theme,
    *,
    fade_rgb: tuple[int, int, int] | None = None,
    fade_h: int | None = None,
) -> None:
    """Edge fade overlays when a clipped viewport has off-screen content."""
    bottom_strength = scroll.edge_hint_strength("bottom")
    top_strength = scroll.edge_hint_strength("top")
    if bottom_strength <= 0.008 and top_strength <= 0.008:
        return
    rgb, default_fade_h, alpha_power, max_opacity, show_chevron = _scroll_hint_style(
        theme,
        fade_rgb,
    )
    band_h = fade_h if fade_h is not None else default_fade_h
    if bottom_strength > 0.008:
        _draw_vertical_edge_fade(
            surface,
            viewport,
            rgb,
            edge="bottom",
            fade_h=band_h,
            alpha_power=alpha_power,
            strength=bottom_strength,
            max_opacity=max_opacity,
        )
        if show_chevron:
            _draw_scroll_edge_chevron(
                surface,
                viewport,
                theme.muted,
                edge="bottom",
                strength=bottom_strength,
            )
    if top_strength > 0.008:
        _draw_vertical_edge_fade(
            surface,
            viewport,
            rgb,
            edge="top",
            fade_h=band_h,
            alpha_power=alpha_power,
            strength=top_strength,
            max_opacity=max_opacity,
        )
        if show_chevron:
            _draw_scroll_edge_chevron(
                surface,
                viewport,
                theme.muted,
                edge="top",
                strength=top_strength,
            )


DANGER_ACTION_IDS = frozenset(
    {
        "qa_delete",
        "qa_delete_confirm",
        "unfavorite_confirm",
        "qa_remove_all_confirm",
    }
)


class ActionSheetRow:
    """One row in a scrollable bottom-sheet action list (content-local coordinates)."""

    __slots__ = ("action_id", "label", "content_rect", "is_section")

    def __init__(
        self,
        action_id: str,
        label: str,
        content_rect: Rect,
        *,
        is_section: bool = False,
    ) -> None:
        self.action_id = action_id
        self.label = label
        self.content_rect = content_rect
        self.is_section = is_section


class ScrollableActionList:
    """
    Bottom-sheet modal with a fixed title and vertically scrollable action rows.

    Use for context menus, pickers, and any touch list that can exceed viewport height.
    Pointer routing: pointer_down → pointer_move → pointer_up; call action_at only when
    pointer_up reports no scroll gesture.
    """

    HEADER_H = 44
    BOTTOM_PAD = 12

    def __init__(self) -> None:
        self.panel = Rect(0, 0, 0, 0)
        self.scroll_viewport = Rect(0, 0, 0, 0)
        self.scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self.rows: list[ActionSheetRow] = []
        self.pressed_action_id: str | None = None

    @property
    def is_scrollable(self) -> bool:
        return self.scroll._max_scroll_pixels() > 0

    def layout(
        self,
        *,
        screen_w: int,
        screen_h: int,
        margin: int,
        bottom_margin: int,
        actions: list[tuple[str, str]],
        row_h: int = 52,
        section_h: int = 28,
        gap: int = 8,
        max_body_ratio: float = 0.55,
    ) -> None:
        body_h = 0
        for index, (action_id, _label) in enumerate(actions):
            body_h += section_h if action_id == "_section" else row_h
            if index < len(actions) - 1:
                body_h += gap

        max_panel_h = max(120, screen_h - margin * 2 - 80)
        max_body = min(int(screen_h * max_body_ratio), max_panel_h - self.HEADER_H - self.BOTTOM_PAD)
        visible_body = min(body_h, max(80, max_body))

        panel_h = self.HEADER_H + visible_body + self.BOTTOM_PAD
        panel_w = screen_w - margin * 2
        panel_y = screen_h - panel_h - bottom_margin
        self.panel = Rect(margin, panel_y, panel_w, panel_h)

        inner_w = panel_w - 24
        scroll_x = self.panel.x + 12
        scroll_y = self.panel.y + self.HEADER_H
        self.scroll_viewport = Rect(scroll_x, scroll_y, inner_w, visible_body)
        self.scroll.viewport = self.scroll_viewport
        self.scroll.content_height = body_h
        self.scroll.reset()

        self.rows = []
        y = 0
        for index, (action_id, label) in enumerate(actions):
            h = section_h if action_id == "_section" else row_h
            self.rows.append(
                ActionSheetRow(
                    action_id,
                    label,
                    Rect(0, y, inner_w, h),
                    is_section=action_id == "_section",
                )
            )
            y += h
            if index < len(actions) - 1:
                y += gap

    def contains(self, pos: tuple[int, int]) -> bool:
        return self.panel.contains(*pos)

    def action_at(self, pos: tuple[int, int]) -> str | None:
        if not self.scroll_viewport.contains(*pos):
            return None
        scroll = int(self.scroll.scroll_pixels)
        local_x = pos[0] - self.scroll_viewport.x
        local_y = pos[1] - self.scroll_viewport.y + scroll
        for row in self.rows:
            if row.is_section:
                continue
            if row.content_rect.contains(local_x, local_y):
                return row.action_id
        return None

    def pointer_down(self, pos: tuple[int, int]) -> bool:
        self.pressed_action_id = self.action_at(pos)
        if self.scroll_viewport.contains(*pos):
            return self.scroll.pointer_down(pos)
        return self.pressed_action_id is not None

    def pointer_move(self, pos: tuple[int, int]) -> bool:
        moved = self.scroll.pointer_move(pos)
        if moved and self.scroll._pointer_scrolled:
            self.pressed_action_id = None
        return moved

    def pointer_up(self, pos: tuple[int, int]) -> bool:
        scrolled = self.scroll.pointer_up(pos)
        self.pressed_action_id = None
        return scrolled

    def tick(self, dt: float) -> bool:
        self.scroll.tick_edge_hints(dt)
        return self.scroll.tick(dt)

    def _draw_scroll_hints(self, surface: pygame.Surface, theme) -> None:
        draw_vertical_scroll_edge_hints(
            surface,
            self.scroll_viewport,
            self.scroll,
            theme,
        )

    def draw(
        self,
        surface: pygame.Surface,
        *,
        title: str,
        font_md: pygame.font.Font,
        font_sm: pygame.font.Font,
        theme,
        draw_elevated_panel,
        selected_action_id: str | None = None,
    ) -> None:
        draw_elevated_panel(self.panel, border_radius=16)
        title_s = font_md.render(title, True, theme.text)
        surface.blit(title_s, (self.panel.x + 16, self.panel.y + 12))

        clip = surface.get_clip()
        surface.set_clip(self.scroll_viewport.pygame_rect)
        scroll = int(self.scroll.scroll_pixels)
        for row in self.rows:
            screen_y = self.scroll_viewport.y + row.content_rect.y - scroll
            screen_rect = Rect(
                self.scroll_viewport.x,
                screen_y,
                row.content_rect.w,
                row.content_rect.h,
            )
            if screen_rect.bottom < self.scroll_viewport.y or screen_rect.y > self.scroll_viewport.bottom:
                continue
            if row.is_section:
                surf = font_sm.render(row.label, True, theme.muted)
                surface.blit(surf, (screen_rect.x + 4, screen_rect.y + 6))
                continue
            pressed = row.action_id == self.pressed_action_id
            selected = (
                not pressed
                and selected_action_id is not None
                and row.action_id == selected_action_id
            )
            if pressed:
                bg = theme.accent
                color = theme.bg
            else:
                bg = theme.surface_alt
                color = theme.danger if row.action_id in DANGER_ACTION_IDS else theme.text
            pygame.draw.rect(surface, bg, screen_rect.pygame_rect, border_radius=10)
            if selected:
                pygame.draw.rect(
                    surface,
                    theme.accent,
                    screen_rect.pygame_rect,
                    width=2,
                    border_radius=10,
                )
            surf = font_md.render(row.label, True, color)
            ty = screen_rect.y + (screen_rect.h - surf.get_height()) // 2
            surface.blit(surf, (screen_rect.x + 14, ty))
        surface.set_clip(clip)
        self._draw_scroll_hints(surface, theme)
