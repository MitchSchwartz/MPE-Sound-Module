"""Shared pressed-state tracking for touch targets."""

from __future__ import annotations


class TouchPressState:
    """Which tappable control is currently held (pointer down until up)."""

    __slots__ = ("active_id",)

    def __init__(self) -> None:
        self.active_id: str | None = None

    def clear(self) -> None:
        self.active_id = None

    def set(self, target_id: str | None) -> None:
        self.active_id = target_id

    def is_pressed(self, target_id: str) -> bool:
        return self.active_id == target_id
