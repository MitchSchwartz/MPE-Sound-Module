"""Shared UI colors for pygame surfaces (touch browser, calibration loader)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Theme:
    bg: tuple[int, int, int] = (10, 10, 12)
    surface: tuple[int, int, int] = (22, 22, 28)
    surface_alt: tuple[int, int, int] = (32, 32, 40)
    text: tuple[int, int, int] = (232, 232, 236)
    muted: tuple[int, int, int] = (130, 130, 140)
    accent: tuple[int, int, int] = (107, 159, 255)
    playing: tuple[int, int, int] = (255, 180, 90)
    danger: tuple[int, int, int] = (220, 90, 90)
    ok: tuple[int, int, int] = (90, 200, 140)
