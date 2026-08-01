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
    """Flat A→Z browse — stacked A/Z with index rail."""
    rail_x = rect.right - 5
    pygame.draw.line(
        surface,
        color,
        (rail_x, rect.y + 3),
        (rail_x, rect.bottom - 3),
        2,
    )
    text_w = max(1, rect.w - 10)
    a = font.render("A", True, color)
    z = font.render("Z", True, color)
    ax = rect.x + (text_w - a.get_width()) // 2
    zx = rect.x + (text_w - z.get_width()) // 2
    ay = rect.y + 1
    zy = rect.bottom - z.get_height() - 1
    surface.blit(a, (ax, ay))
    surface.blit(z, (zx, zy))
    mid_y = (ay + a.get_height() + zy) // 2
    pygame.draw.line(surface, color, (rect.x + 4, mid_y), (rail_x - 3, mid_y), 1)


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
