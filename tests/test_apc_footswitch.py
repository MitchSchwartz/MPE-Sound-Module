"""APC footswitch tap / quantize / master gating."""

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
        footswitch_mod._master_sync_mode = None
        self.osc = MagicMock()
        self.midi = MagicMock()
        self.fs = LoopFootswitch(loop=1, hold_ms=2000.0, debounce_ms=0.0, num_loops=16)
        self.fs.bind(self.osc, self.midi, note=36)

    def test_slave_record_blocked_without_master(self) -> None:
        self.fs._tap()
        self.osc.send_message.assert_not_called()
        self.assertEqual(self.fs.state, STATE_IDLE)

    @patch.object(footswitch_mod, "_refresh_grid_sync")
    @patch.object(footswitch_mod, "_capture_master_clock_from_hud")
    def test_master_established_after_loop0_record(
        self, _capture: MagicMock, _refresh: MagicMock
    ) -> None:
        master = LoopFootswitch(loop=0, hold_ms=2000.0, debounce_ms=0.0, num_loops=16)
        master.bind(self.osc, self.midi, note=0)
        master._tap()  # start record
        master._tap()  # end record -> playing + establish master
        self.assertTrue(master_loop_established())
        self.assertEqual(master.state, STATE_PLAYING)

    @patch.object(footswitch_mod, "_ensure_master_playing")
    def test_slave_end_record_waits_for_quantize(self, _ensure: MagicMock) -> None:
        footswitch_mod._master_loop_established = True
        self.fs.state = STATE_RECORDING
        self.fs._tap()
        self.assertTrue(self.fs.awaiting_quantize)
        self.assertEqual(self.fs.state, STATE_RECORDING)
        self.fs._tap()
        self.osc.send_message.assert_called_once()

    def test_quantize_wait_blocks_second_tap(self) -> None:
        footswitch_mod._master_loop_established = True
        self.fs.state = STATE_RECORDING
        self.fs.awaiting_quantize = True
        self.fs._tap()
        self.osc.send_message.assert_not_called()

    def test_sync_from_sl_clears_wait_on_playing(self) -> None:
        self.fs.state = STATE_RECORDING
        self.fs.awaiting_quantize = True
        changed = self.fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(changed)
        self.assertFalse(self.fs.awaiting_quantize)
        self.assertEqual(self.fs.state, STATE_PLAYING)

    def test_sync_from_sl_keeps_red_during_wait_stop(self) -> None:
        self.fs.state = STATE_RECORDING
        changed = self.fs.sync_from_sl(SL_STATE_WAIT_STOP)
        self.assertTrue(changed)
        self.assertTrue(self.fs.awaiting_quantize)


if __name__ == "__main__":
    unittest.main()
