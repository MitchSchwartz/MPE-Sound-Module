"""APC footswitch tap / quantize behavior."""

import unittest
from unittest.mock import MagicMock, patch

import scripts.sooperlooper.apc_footswitch as footswitch_mod
from scripts.sooperlooper.apc_footswitch import (
    LoopFootswitch,
    STATE_IDLE,
    STATE_PLAYING,
    STATE_RECORDING,
    master_loop_established,
)
from scripts.sooperlooper.sl_loop_states import SL_STATE_PLAYING, SL_STATE_WAIT_STOP


class LoopFootswitchTapTests(unittest.TestCase):
    def setUp(self) -> None:
        footswitch_mod._master_loop_established = False
        self.osc = MagicMock()
        self.midi = MagicMock()
        self.fs = LoopFootswitch(loop=1, hold_ms=2000.0, debounce_ms=0.0, num_loops=16)
        self.fs.bind(self.osc, self.midi, note=36)

    @patch.object(footswitch_mod, "_schedule_grid_sync")
    def test_master_triggers_playback_after_record(self, _schedule: MagicMock) -> None:
        master = LoopFootswitch(loop=0, hold_ms=2000.0, debounce_ms=0.0, num_loops=16)
        master.bind(self.osc, self.midi, note=0)
        master._tap()
        master._tap()
        self.assertTrue(master_loop_established())
        self.assertEqual(master.state, STATE_PLAYING)
        calls = [c.args[1] for c in self.osc.send_message.call_args_list]
        self.assertEqual(calls.count("record"), 2)
        self.assertEqual(calls.count("trigger"), 1)
        _schedule.assert_called_once()

    @patch.object(footswitch_mod, "_ensure_master_playing")
    def test_slave_end_record_waits_for_quantize(self, _ensure: MagicMock) -> None:
        footswitch_mod._master_loop_established = True
        self.fs.state = STATE_RECORDING
        self.fs._tap()
        self.assertTrue(self.fs.awaiting_quantize)
        self.assertEqual(self.fs.state, STATE_RECORDING)
        self.fs._tap()
        self.osc.send_message.assert_called_once()

    def test_quantize_wait_blocks_second_tap_on_slave(self) -> None:
        footswitch_mod._master_loop_established = True
        self.fs.state = STATE_RECORDING
        self.fs.awaiting_quantize = True
        self.fs._tap()
        self.osc.send_message.assert_not_called()

    def test_sync_from_sl_ignored_on_loop0(self) -> None:
        master = LoopFootswitch(loop=0, hold_ms=2000.0, debounce_ms=0.0, num_loops=16)
        master.bind(self.osc, self.midi, note=0)
        master.state = STATE_PLAYING
        self.assertFalse(master.sync_from_sl(SL_STATE_WAIT_STOP))

    def test_sync_from_sl_clears_wait_on_playing(self) -> None:
        self.fs.state = STATE_RECORDING
        self.fs.awaiting_quantize = True
        changed = self.fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(changed)
        self.assertFalse(self.fs.awaiting_quantize)
        self.assertEqual(self.fs.state, STATE_PLAYING)


if __name__ == "__main__":
    unittest.main()
