"""Calibration gestures press with channel pressure, never pitch bend.

Until 2026-10-22 every gesture sent its pressure as 0xE0 (pitch bend). On the
Pi that played the C4 anchor near 66 Hz instead of 262 Hz, with no pressure,
so every stored calibration measured the wrong pitch. These tests pin the
exact message sequence each gesture sends.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CAL_MODULE_PATH = REPO_ROOT / "scripts" / "calibrate-patch-normalization.py"


def load_cal_module():
    spec = importlib.util.spec_from_file_location("calibrate_patch_normalization", CAL_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_patch_normalization"] = module
    spec.loader.exec_module(module)
    return module


CAL = load_cal_module()
CH = CAL.MPE_CHANNEL - 1
CENTRE = [0xE0 | CH, 0x00, 0x40]
ON = 0x90 | CH
OFF = 0x80 | CH
PRESS = 0xD0 | CH


def sent(gesture, **kwargs) -> list[list[int]]:
    out = mock.Mock()
    with mock.patch.object(CAL, "time") as time_mock:
        time_mock.sleep = mock.Mock()
        gesture(out, **kwargs)
    return [list(call.args[0]) for call in out.send_message.call_args_list]


class GesturesPressNotBend(unittest.TestCase):
    def test_anchor_gesture_sequence(self) -> None:
        for velocity, pressure in ((CAL.STRIKE_VELOCITY, CAL.STRIKE_ANCHOR_PRESSURE),
                                   (100, CAL.SUSTAIN_ANCHOR_PRESSURE)):
            with self.subTest(pressure=pressure):
                self.assertEqual(
                    sent(CAL.send_anchor_gesture, velocity=velocity, pressure=pressure,
                         pre_roll=0.0),
                    [CENTRE, [ON, CAL.NOTE, velocity], [PRESS, pressure],
                     [OFF, CAL.NOTE, 0]],
                )

    def test_light_touch_gesture_sequence(self) -> None:
        self.assertEqual(
            sent(CAL.send_light_touch_gesture, pre_roll=0.0),
            [CENTRE, [ON, CAL.NOTE, CAL.STRIKE_VELOCITY],
             [PRESS, CAL.LIGHT_TOUCH_PRESSURE], [OFF, CAL.NOTE, 0]],
        )

    def test_performance_gesture_sequence(self) -> None:
        ramp = [[PRESS, int(127 * step / 24)] for step in range(25)]
        self.assertEqual(
            sent(CAL.send_performance_gesture, pre_roll=0.0),
            [CENTRE, [ON, CAL.NOTE, CAL.STRIKE_VELOCITY], *ramp, [OFF, CAL.NOTE, 0]],
        )

    def test_pressure_is_clamped_to_seven_bits(self) -> None:
        self.assertEqual(CAL.pressure_message(CH, 300), [PRESS, 127])
        self.assertEqual(CAL.pressure_message(CH, -5), [PRESS, 0])


if __name__ == "__main__":
    unittest.main()
