"""Mixer fader column model for the touch patch browser."""

from __future__ import annotations

from dataclasses import dataclass

from patch_browser.geometry import Rect

#: Under each fader's track: the value line starts this far below it...
FADER_VALUE_DY = 6
#: ...and the name line this far. The draw and the Effect label's caret
#: (`touch_browser_fx_effect_modal`) both place text from these.
FADER_NAME_DY = 24


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
