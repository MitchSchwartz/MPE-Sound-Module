"""Progressive gesture-length retry: escalate hold time when a patch measures too quiet."""

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


class HoldSecondsForGestureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_base_gesture_uses_default_hold(self) -> None:
        self.assertEqual(self.cal.hold_seconds_for_gesture(3.0), 1.8)

    def test_longer_gesture_scales_hold_up(self) -> None:
        hold_5s = self.cal.hold_seconds_for_gesture(5.0)
        hold_8s = self.cal.hold_seconds_for_gesture(8.0)
        self.assertGreater(hold_5s, 1.8)
        self.assertGreater(hold_8s, hold_5s)

    def test_hold_never_exceeds_gesture_minus_overhead(self) -> None:
        for gesture in self.cal.GESTURE_DURATIONS_SECONDS:
            hold = self.cal.hold_seconds_for_gesture(gesture)
            # pre_roll(0.25) + hold + tail(0.35) must fit inside the ffmpeg -t window.
            self.assertLessEqual(hold + 0.25 + 0.35, gesture)


class ProgressiveRetryEscalationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_first_duration_equals_base_gesture_seconds(self) -> None:
        self.assertEqual(self.cal.GESTURE_DURATIONS_SECONDS[0], self.cal.GESTURE_SECONDS)

    def test_durations_strictly_increase(self) -> None:
        durations = self.cal.GESTURE_DURATIONS_SECONDS
        self.assertEqual(list(durations), sorted(durations))
        self.assertEqual(len(set(durations)), len(durations))

    def test_measure_max_attempts_matches_duration_count(self) -> None:
        self.assertEqual(self.cal.MEASURE_MAX_ATTEMPTS, len(self.cal.GESTURE_DURATIONS_SECONDS))

    def test_capture_gesture_wav_passes_scaled_hold_to_gesture(self) -> None:
        fake_out = mock.Mock()
        with (
            mock.patch.object(self.cal.subprocess, "Popen") as popen_mock,
            mock.patch.object(self.cal, "send_performance_gesture") as gesture_mock,
            mock.patch.object(self.cal.time, "sleep"),
        ):
            popen_mock.return_value.returncode = 0
            self.cal.capture_gesture_wav(fake_out, "plughw:0,0", gesture_seconds=8.0)

        gesture_mock.assert_called_once()
        _, kwargs = gesture_mock.call_args
        self.assertEqual(kwargs["hold_seconds"], self.cal.hold_seconds_for_gesture(8.0))
        # ffmpeg invoked with the escalated -t duration, not the fixed base default.
        ffmpeg_args = popen_mock.call_args[0][0]
        t_index = ffmpeg_args.index("-t")
        self.assertEqual(ffmpeg_args[t_index + 1], "8.0")


if __name__ == "__main__":
    unittest.main()
