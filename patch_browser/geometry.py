"""Shared rectangle helper for pygame touch UIs."""

from __future__ import annotations

from dataclasses import dataclass

import pygame


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def centerx(self) -> int:
        return self.x + self.w // 2

    @property
    def centery(self) -> int:
        return self.y + self.h // 2

    @property
    def pygame_rect(self) -> pygame.Rect:
        return pygame.Rect(self.x, self.y, self.w, self.h)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h
