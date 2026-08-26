"""SL HUD bar sweep, state reader, and tempo seed tests (consolidated)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from patch_browser.looper_hud import (
    bar_progress,
    bar_seconds,
    beat_label,
    current_beat_index,
    interpolated_pos,
    is_running,
    phrase_seconds,
    segment_count,
    should_show,
)
from patch_browser.sl_hud_state import read_sl_hud_state
from scripts.sooperlooper.sl_hud_monitor import beat_and_bar

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _writer_with_stub_sl(cached_tempo):
    """A HudWriter whose OSC layer is a stub, without touching the network."""
    import sl_hud_monitor

    writer = sl_hud_monitor.HudWriter.__new__(sl_hud_monitor.HudWriter)
    sl = MagicMock()
    sl.cached.return_value = cached_tempo

    def _seed():
        if sl.cached("tempo", -1) is None:
            sl.get("tempo", -1)

    sl.register_hud = MagicMock()
    sl.seed_tempo = MagicMock(side_effect=_seed)
    writer._sl = sl
    writer._registered_at = 0.0
    return writer


class TempoSeedTests(unittest.TestCase):
    def test_seeds_tempo_when_no_auto_update_has_arrived(self) -> None:
        writer = _writer_with_stub_sl(cached_tempo=None)
        writer.register_auto_updates()
        writer._sl.get.assert_called_once_with("tempo", -1)

    def test_does_not_re_seed_once_tempo_is_known(self) -> None:
        """Re-registration runs every 15 s; it must not blocking-get every time."""
        writer = _writer_with_stub_sl(cached_tempo=120.0)
        writer.register_auto_updates()
        writer._sl.get.assert_not_called()

    def test_still_registers_the_auto_updates(self) -> None:
        """Seeding is additional to the subscription, not a replacement for it."""
        writer = _writer_with_stub_sl(cached_tempo=None)
        writer.register_auto_updates()
        writer._sl.register_hud.assert_called_once()
        writer._sl.seed_tempo.assert_called_once()

    def test_tempo_is_queried_as_a_global_control(self) -> None:
        """Loop -1 maps to the engine-wide key; loop 0 would query loop zero instead."""
        writer = _writer_with_stub_sl(cached_tempo=None)
        writer.register_auto_updates()
        _ctrl, loop = writer._sl.get.call_args.args
        self.assertLess(loop, 0, "tempo must be fetched as a global, not per-loop")

    def test_should_reregister_after_interval(self) -> None:
        import sl_hud_monitor
        import time

        writer = _writer_with_stub_sl(cached_tempo=120.0)
        writer._registered_at = time.monotonic() - (sl_hud_monitor.REREGISTER_INTERVAL_S + 1)
        self.assertTrue(writer.should_reregister())
        writer._registered_at = time.monotonic()
        self.assertFalse(writer.should_reregister())


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


class BarMathTests(unittest.TestCase):
    def test_bar_seconds_from_bpm(self) -> None:
        self.assertAlmostEqual(bar_seconds(120.0), 2.0)
        self.assertAlmostEqual(bar_seconds(40.0), 6.0)
        self.assertIsNone(bar_seconds(0.0))
        self.assertIsNone(bar_seconds(None))

    def test_progress_wraps_once_per_bar(self) -> None:
        sl = {"bpm": 120.0, "loop_pos": 0.0, "updated_at": 1000.0}
        self.assertAlmostEqual(bar_progress(sl, now=1000.0), 0.0)
        self.assertAlmostEqual(bar_progress(sl, now=1001.0), 0.5)
        self.assertAlmostEqual(bar_progress(sl, now=1002.0), 0.0)  # wrapped
        self.assertAlmostEqual(bar_progress(sl, now=1002.5), 0.25)

    def test_position_is_interpolated_between_file_writes(self) -> None:
        """The HUD file updates ~2x/sec; without this the sweep visibly steps."""
        sl = {"bpm": 120.0, "loop_pos": 0.25, "updated_at": 1000.0}
        self.assertAlmostEqual(interpolated_pos(sl, now=1000.0), 0.25)
        self.assertAlmostEqual(interpolated_pos(sl, now=1000.4), 0.65)

    def test_progress_none_without_a_grid(self) -> None:
        self.assertIsNone(bar_progress({}, now=1.0))
        self.assertIsNone(bar_progress({"bpm": 120.0}, now=1.0))


class PhraseTests(unittest.TestCase):
    """The display cycle is the longest clip, not one bar."""

    def test_phrase_spans_the_longest_clip(self) -> None:
        sl = {"bpm": 120.0, "phrase_len": 8.0, "bars_in_phrase": 4}
        self.assertAlmostEqual(phrase_seconds(sl), 8.0)
        self.assertEqual(segment_count(sl), 16)

    def test_phrase_falls_back_to_one_bar_before_any_clip(self) -> None:
        self.assertAlmostEqual(phrase_seconds({"bpm": 120.0}), 2.0)
        self.assertEqual(segment_count({"bpm": 120.0}), 4)

    def test_sweep_fills_the_whole_phrase_not_just_the_first_bar(self) -> None:
        sl = {"bpm": 120.0, "phrase_len": 8.0, "bars_in_phrase": 4,
              "loop_pos": 0.0, "updated_at": 1000.0}
        self.assertAlmostEqual(bar_progress(sl, now=1002.0), 0.25)
        self.assertAlmostEqual(bar_progress(sl, now=1006.0), 0.75)
        self.assertAlmostEqual(bar_progress(sl, now=1007.99), 0.99875)

    def test_live_segment_advances_discretely(self) -> None:
        sl = {"bpm": 120.0, "phrase_len": 8.0, "bars_in_phrase": 4,
              "loop_pos": 0.0, "updated_at": 1000.0}
        self.assertEqual(current_beat_index(sl, now=1000.0), 0)
        self.assertEqual(current_beat_index(sl, now=1000.6), 1)
        self.assertEqual(current_beat_index(sl, now=1007.9), 15)


class LabelAndVisibilityTests(unittest.TestCase):
    def test_label_counts_bars_within_the_phrase(self) -> None:
        self.assertEqual(beat_label({"bar": 3, "bars_in_phrase": 4}), "3/4")
        self.assertEqual(beat_label({"bar": 1, "bars_in_phrase": 1}), "1/1")
        self.assertEqual(beat_label({}), "")

    def test_shown_once_a_grid_exists_even_before_playback(self) -> None:
        self.assertTrue(should_show({"bpm": 120.0}))
        self.assertFalse(should_show({}))
        self.assertFalse(should_show({"bpm": 120.0}, user_enabled=False))

    def test_running_follows_engine_state(self) -> None:
        self.assertTrue(is_running({"active": True}))
        self.assertTrue(is_running({"playing": True}))
        self.assertFalse(is_running({"active": False, "playing": False}))


if __name__ == "__main__":
    unittest.main()
