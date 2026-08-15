"""SL HUD + JACK timebase spike math."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.sl_hud_state import read_sl_hud_state
from scripts.sooperlooper.sl_hud_monitor import beat_and_bar, beat_and_bar_from_transport


class BeatAndBarTests(unittest.TestCase):
    def test_quarter_notes_in_cycle(self) -> None:
        beat, bar = beat_and_bar(0.0, 2.0)
        self.assertEqual(beat, 1)
        self.assertEqual(bar, 1)
        beat, bar = beat_and_bar(0.5, 2.0)
        self.assertEqual(beat, 2)
        beat, bar = beat_and_bar(1.5, 2.0)
        self.assertEqual(beat, 4)
        beat, bar = beat_and_bar(2.5, 2.0)
        self.assertEqual(bar, 2)


class ReadSlHudStateTests(unittest.TestCase):
    def test_transport_source_uses_producer_flags(self) -> None:
        payload = {
            "updated_at": 1000.0,
            "source": "jack_transport",
            "playing": True,
            "has_master": True,
            "active": True,
            "beat": 2,
            "bar": 1,
            "state": 4,
            "bpm": 120.0,
            "loop_len": 0.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hud.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("patch_browser.sl_hud_state.SL_HUD_STATE_FILE", path):
                snap = read_sl_hud_state(now=1000.5)
        self.assertTrue(snap["active"])
        self.assertTrue(snap["has_master"])
        self.assertEqual(snap["beat"], 2)

    def test_legacy_loop_len_gate(self) -> None:
        payload = {
            "updated_at": 1000.0,
            "loop_len": 0.0,
            "state": 4,
            "beat": 1,
            "bar": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hud.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("patch_browser.sl_hud_state.SL_HUD_STATE_FILE", path):
                snap = read_sl_hud_state(now=1000.5)
        self.assertFalse(snap["active"])

if __name__ == "__main__":
    unittest.main()
