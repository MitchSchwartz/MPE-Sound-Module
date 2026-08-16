"""Shared pygame draw helpers for touch UI icons."""

from __future__ import annotations

import pygame

from patch_browser.geometry import Rect


def draw_chevron(
    surface: pygame.Surface,
    rect: Rect,
    color: tuple[int, int, int],
    *,
    direction: str,
) -> None:
    cx, cy = rect.centerx, rect.centery
    if direction == "left":
        points = [(cx + 5, cy - 8), (cx - 5, cy), (cx + 5, cy + 8)]
    else:
        points = [(cx - 5, cy - 8), (cx + 5, cy), (cx - 5, cy + 8)]
    pygame.draw.lines(surface, color, False, points, 3)


def draw_current_patch_icon(
    surface: pygame.Surface,
    rect: Rect,
    color: tuple[int, int, int],
) -> None:
    """Jump to loaded patch folder — folder outline with crosshair."""
    pad = 2
    cx = rect.centerx
    body_w = max(16, rect.w - pad * 2 - 2)
    body_h = max(10, rect.h - pad * 2 - 4)
    tab_w = max(8, body_w // 3)
    tab_h = max(4, body_h // 3)
    body_x = cx - body_w // 2
    body_y = rect.y + pad + tab_h - 1
    tab_x = body_x + 2
    tab_y = body_y - tab_h + 1

    tab = pygame.Rect(tab_x, tab_y, tab_w, tab_h)
    body = pygame.Rect(body_x, body_y, body_w, body_h)
    pygame.draw.rect(surface, color, tab, width=2, border_radius=1)
    pygame.draw.rect(surface, color, body, width=2, border_radius=2)

    icx, icy = body.centerx, body.centery
    arm = min(body.w, body.h) // 2 + 2
    pygame.draw.circle(surface, color, (icx, icy), max(3, arm // 2), 2)
    pygame.draw.circle(surface, color, (icx, icy), 2)
    tick = max(2, arm // 3)
    for x1, y1, x2, y2 in (
        (icx, icy - arm, icx, icy - tick),
        (icx, icy + tick, icx, icy + arm),
        (icx - arm, icy, icx - tick, icy),
        (icx + tick, icy, icx + arm, icy),
    ):
        pygame.draw.line(surface, color, (x1, y1), (x2, y2), 2)


def draw_all_patches_icon(
    surface: pygame.Surface,
    rect: Rect,
    color: tuple[int, int, int],
    font: pygame.font.Font,
) -> None:
    """Flat A→Z browse — simple A-Z label."""
    label = font.render("A-Z", True, color)
    tx = rect.x + (rect.w - label.get_width()) // 2
    ty = rect.y + (rect.h - label.get_height()) // 2
    surface.blit(label, (tx, ty))


def draw_filter_icon(
    surface: pygame.Surface,
    rect: Rect,
    color: tuple[int, int, int],
) -> None:
    """Instrument filter — three-line filter-list icon (nav header toggle)."""
    pad_x = max(4, rect.w // 6)
    pad_y = max(4, rect.h // 6)
    line_h = max(2, rect.h // 10)
    usable_h = rect.h - pad_y * 2
    gap = max(2, (usable_h - line_h * 3) // 2)
    max_w = max(8, rect.w - pad_x * 2)
    y = rect.y + pad_y
    for width_frac in (1.0, 0.72, 0.48):
        line_w = max(6, int(max_w * width_frac))
        x = rect.x + (rect.w - line_w) // 2
        pygame.draw.rect(surface, color, pygame.Rect(x, y, line_w, line_h))
        y += line_h + gap


def draw_toggle_switch(
    surface: pygame.Surface,
    rect: Rect,
    *,
    on: bool,
    track_on: tuple[int, int, int],
    track_off: tuple[int, int, int],
    knob_color: tuple[int, int, int],
    border_color: tuple[int, int, int] | None = None,
) -> None:
    """Pill track + sliding knob (settings toggles)."""
    track_color = track_on if on else track_off
    pygame.draw.rect(surface, track_color, rect.pygame_rect, border_radius=rect.h // 2)
    if border_color is not None:
        pygame.draw.rect(
            surface,
            border_color,
            rect.pygame_rect,
            width=2,
            border_radius=rect.h // 2,
        )
    knob_pad = max(2, min(3, rect.h // 8))
    knob_size = rect.h - knob_pad * 2
    knob_x = rect.right - knob_pad - knob_size if on else rect.x + knob_pad
    knob_y = rect.y + knob_pad
    pygame.draw.rect(
        surface,
        knob_color,
        pygame.Rect(knob_x, knob_y, knob_size, knob_size),
        border_radius=knob_size // 2,
    )


def draw_sidebar_panel_icon(
    surface: pygame.Surface,
    rect: Rect,
    color: tuple[int, int, int],
    *,
    panel_open: bool,
) -> None:
    """Sidebar panel open/close — split layout icon (not a plain back chevron)."""
    pad = 6
    ix = rect.x + pad
    iy = rect.y + (rect.h - 14) // 2
    iw = max(18, rect.w - pad * 2)
    ih = 14
    split_x = ix + max(6, int(iw * 0.36))

    frame = pygame.Rect(ix, iy, iw, ih)
    pygame.draw.rect(surface, color, frame, width=2, border_radius=2)
    pygame.draw.line(surface, color, (split_x, iy + 2), (split_x, iy + ih - 2), 2)

    cy = iy + ih // 2
    if panel_open:
        sidebar = pygame.Rect(ix + 2, iy + 2, split_x - ix - 3, ih - 4)
        pygame.draw.rect(surface, color, sidebar, border_radius=1)
        cx = split_x + (ix + iw - split_x) // 2 + 1
        for dx in (4, 9):
            points = [(cx + dx, cy - 4), (cx + dx - 4, cy), (cx + dx, cy + 4)]
            pygame.draw.lines(surface, color, False, points, 2)
    else:
        strip_w = max(4, int(iw * 0.14))
        strip = pygame.Rect(ix + 2, iy + 2, strip_w, ih - 4)
        pygame.draw.rect(surface, color, strip, border_radius=1)
        cx = ix + strip_w + (iw - strip_w) // 2
        for dx in (-4, -9):
            points = [(cx + dx, cy - 4), (cx + dx + 4, cy), (cx + dx, cy + 4)]
            pygame.draw.lines(surface, color, False, points, 2)


def draw_lock_icon(
    surface: pygame.Surface,
    rect: Rect,
    color: tuple[int, int, int],
) -> None:
    """Small padlock — secured Wi‑Fi indicator."""
    cx, cy = rect.centerx, rect.centery
    body_w = max(8, min(rect.w - 2, 12))
    body_h = max(6, min(rect.h // 2, 9))
    body_x = cx - body_w // 2
    body_y = cy + 1
    pygame.draw.rect(surface, color, (body_x, body_y, body_w, body_h), border_radius=2)

    shackle_w = body_w + 4
    shackle_h = max(7, body_h + 2)
    shackle_rect = pygame.Rect(cx - shackle_w // 2, body_y - shackle_h + 3, shackle_w, shackle_h * 2)
    pygame.draw.arc(surface, color, shackle_rect, 3.14159, 0.0, 2)
