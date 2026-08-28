"""Multigrid delegates record/close/stop to TrackGesture — not parallel OSC."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from track_gesture import TrackGesture  # noqa: E402
from apc_grid import pad_note  # noqa: E402
from sl_loop_states import SL_STATE_OFF, SL_STATE_RECORDING  # noqa: E402


class _OscStub:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def send_message(self, path, args) -> None:
        if isinstance(args, str):
            self._sink.append((path, [args]))
        else:
            self._sink.append((path, list(args)))


def build_test_gesture(
    loop: int, sink: list, *, debounce_ms: float = 0
) -> TrackGesture:
    """`debounce_ms` is a parameter on purpose.

    It defaulted to 0 with no way to override, and every harness in the suite
    inherited that. 0 is the one value at which a debounce bug cannot appear,
    and the appliance runs 200 — which is how a scene launch that was silently
    swallowed by the debounce shipped with a green suite.
    """
    fs = TrackGesture(
        loop=loop,
        hold_ms=2000,
        debounce_ms=debounce_ms,
        multigrid=True,
        quantized=False,
    )
    fs.bind(_OscStub(sink), None, pad_note(0, loop))
    return fs


class DelegationTests(unittest.TestCase):
    def test_close_take_uses_gesture_overdub_path(self) -> None:
        sink: list[tuple[str, list]] = []
        fs = build_test_gesture(0, sink)
        fs.sl_state = SL_STATE_RECORDING
        fs.on_pad_down()
        hits = [a for p, a in sink if p.endswith("/hit")]
        self.assertIn(["overdub"], hits)

    def test_arm_record_sends_record_via_gesture(self) -> None:
        sink: list[tuple[str, list]] = []
        fs = build_test_gesture(3, sink)
        fs.sl_state = SL_STATE_OFF
        fs.on_pad_down()
        self.assertIn(("/sl/3/hit", ["record"]), sink)
