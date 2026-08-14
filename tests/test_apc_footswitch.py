"""APC footswitch — no master-loop special cases."""

import unittest
from unittest.mock import MagicMock

import scripts.sooperlooper.apc_footswitch as footswitch_mod
from scripts.sooperlooper.apc_footswitch import LoopFootswitch, build_footswitches
from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)


class ApcFootswitchTests(unittest.TestCase):
    def test_loop0_tap_record_does_not_send_trigger(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        fs.on_pad_down()
        fs.on_pad_up()
        paths = [c.args[0] for c in osc.send_message.call_args_list]
        self.assertIn("/sl/0/hit", paths)
        self.assertEqual(paths.count("/sl/0/hit"), 1)
        self.assertNotIn("trigger", [c.args[1] for c in osc.send_message.call_args_list])

    def test_sync_from_sl_loop0_playing(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        changed = fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(changed)
        self.assertEqual(fs.state, "playing")

    def test_sync_from_sl_quantize_wait_stays_red(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=2, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 38)
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        self.assertEqual(fs.state, "recording")
        self.assertTrue(fs.awaiting_quantize)

    def test_second_tap_reaches_sl_while_armed(self) -> None:
        """Armed but not yet recording, a second tap must reach SL as cancel.

        Swallowing it is what made the pad feel dead when the grid clock was
        not running: SL parks in WAIT_START and the tap goes nowhere.
        """
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        fs.on_pad_down()
        fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_WAIT_START)
        self.assertFalse(fs.awaiting_quantize)
        fs.on_pad_down()
        fs.on_pad_up()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0] == "/sl/0/hit"]
        self.assertEqual(hits, ["record", "record"])

    def test_quantize_wait_times_out_instead_of_latching(self) -> None:
        """No cycle boundary => release the pad, never latch it forever."""
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        fs.on_pad_down()
        fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.on_pad_down()
        fs.on_pad_up()  # stop-record -> waits for a boundary
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        self.assertTrue(fs.awaiting_quantize)
        self.assertTrue(fs._waiting_for_quantize())

        fs._wait_since -= footswitch_mod.QUANTIZE_WAIT_TIMEOUT_S + 1.0
        self.assertFalse(fs._waiting_for_quantize())
        self.assertFalse(fs.awaiting_quantize)

        fs.on_pad_down()
        fs.on_pad_up()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0] == "/sl/0/hit"]
        self.assertEqual(len(hits), 3)

    def test_sync_from_sl_paused_yellow(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 37)
        fs.sync_from_sl(SL_STATE_PAUSED)
        self.assertEqual(fs.state, "stopped")

    def test_build_includes_loop0(self) -> None:
        osc = MagicMock()
        _, footswitches = build_footswitches(
            osc=osc,
            midi_out=MagicMock(),
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        loops = {fs.loop for fs in footswitches}
        self.assertIn(0, loops)


if __name__ == "__main__":
    unittest.main()
