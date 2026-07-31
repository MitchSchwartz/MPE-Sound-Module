"""Mixer fader column model for the touch patch browser."""

from __future__ import annotations

from dataclasses import dataclass

from patch_browser.geometry import Rect


@dataclass
class MixerChannel:
    """Vertical mixing-board fader column."""

    channel_id: str
    label: str
    min_value: float
    max_value: float
    enabled: bool
    column_rect: Rect
    track_rect: Rect
