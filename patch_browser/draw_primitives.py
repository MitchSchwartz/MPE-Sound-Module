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
