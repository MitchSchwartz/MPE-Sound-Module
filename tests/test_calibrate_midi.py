"""Regression: calibration must not leak ALSA sequencer clients per patch."""

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


class CalibrateMidiReuseTests(unittest.TestCase):
    def test_send_performance_gesture_reuses_provided_midi_out(self) -> None:
        cal = load_cal_module()
        fake_out = mock.Mock()
        with mock.patch.object(cal, "time") as time_mock:
            time_mock.sleep = mock.Mock()
            cal.send_performance_gesture(fake_out, pre_roll=0.0)

        self.assertGreater(fake_out.send_message.call_count, 0)

    def test_find_surge_midi_port_closes_probe_client(self) -> None:
        cal = load_cal_module()
        fake_out = mock.Mock()
        fake_out.get_ports.return_value = ["Surge XT: Input"]
        fake_rtmidi = mock.Mock(MidiOut=mock.Mock(return_value=fake_out))
        with mock.patch.dict(sys.modules, {"rtmidi": fake_rtmidi}):
            port = cal.find_surge_midi_port(announce=False)
        self.assertEqual(port, 0)
        fake_out.close_port.assert_called_once()

    def test_main_closes_shared_midi_out_when_opened(self) -> None:
        cal = load_cal_module()
        fake_out = mock.Mock()
        # PatchNormalizationStore and collect_patch_paths are fully mocked; /tmp/Fake.fxp
        # is a placeholder path only — no real calibration I/O or Pi audio stack.
        with (
            mock.patch.object(cal, "parse_args") as parse_args,
            mock.patch.object(cal, "collect_patch_paths", return_value=[Path("/tmp/Fake.fxp")]),
            mock.patch.object(cal, "PatchNormalizationStore") as store_cls,
            mock.patch.object(cal, "PatchLoader") as loader_cls,
            mock.patch.object(cal, "wait_for_surge_midi_port", return_value=0),
            mock.patch.object(cal, "should_use_loopback", return_value=False),
            mock.patch.object(cal, "detect_capture_device", return_value="plughw:Loopback,1,0"),
            mock.patch.object(cal, "emit_progress"),
            mock.patch.object(cal, "open_midi_out", return_value=fake_out),
            mock.patch.object(cal, "close_midi_out") as close_mock,
            mock.patch.object(cal, "calibrate_patch", return_value=True),
        ):
            store = store_cls.return_value
            store.list_missing.return_value = ["Fake"]
            loader = loader_cls.return_value
            loader.osc_enabled = True
            parse_args.return_value = mock.Mock(
                output=Path("/tmp/test-patch-normalization.json"),
                use_loopback=False,
                audio_device="plughw:Loopback,1,0",
                favorites_only=False,
                folder=None,
                limit=0,
                force=False,
                patch=None,
                mock_lufs=None,
                dry_run=False,
                progress_json=False,
                no_restore_services=True,
                osc_host="127.0.0.1",
                osc_port=53280,
            )
            cal.main()
            close_mock.assert_called_once_with(fake_out)
