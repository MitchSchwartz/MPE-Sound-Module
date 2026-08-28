"""Multigrid mode — gesture must not paint matrix pads."""

from __future__ import annotations

from tests import conftest  # noqa: F401

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.track_gesture import TrackGesture, poll_track_gestures
from scripts.sooperlooper.sl_loop_states import SL_STATE_PLAYING


class MultigridTrackGestureTests(unittest.TestCase):
    def test_poll_track_gestures_skips_hold_and_led_when_multigrid(self) -> None:
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0, multigrid=True)
        fs._pad_down = True
        fs._pad_down_at = 0.0
        fs.poll_hold = MagicMock()
        fs.poll_led = MagicMock()
        poll_track_gestures([fs], multigrid=True)
        fs.poll_hold.assert_not_called()
        # poll_led IS still called. It advances the blink phase, and
        # SlotSurface paints the pad from current_led() — stopping it would
        # freeze every transition blink on the matrix. Painting is suppressed
        # separately, inside _set_led.
        fs.poll_led.assert_called_once()

    def test_sync_from_sl_does_not_paint_led_when_multigrid(self) -> None:
        out = MagicMock()
        fs = TrackGesture(
            loop=0, hold_ms=1000.0, debounce_ms=0.0, multigrid=True
        )
        fs.bind(MagicMock(), out, 36)
        fs.sync_from_sl(SL_STATE_PLAYING)
        out.send_message.assert_not_called()
