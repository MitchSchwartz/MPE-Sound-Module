"""The looper-grid MIDI offset: one computation, from the running graph.

Three defects, all measured on the appliance 2026-09-01.

1. The UI displayed −43 ms while the runtime applied −4 ms. The display called
   buffer_latency_ms(current_buffer_size(), ...), and current_buffer_size() is
   MPE_SURGE_BUFFER_SIZE — the LEGACY Surge ALSA key, 1024 on the appliance —
   whose own docstring says it is "not the playing JACK period". A 10x
   disagreement on the one screen you would read to judge the offset.

2. It was computed from MPE_JACK_BUFFER, the period REQUESTED, not the one the
   server is running. The fallback ladder makes those differ: a 64 -> 256 climb
   leaves the offset 4x wrong, in the direction that fires MIDI late.

3. It was frozen at process start. mpe-pressure-remap computed it once in
   __init__, reads mpe.env only at exec, has Restart=no, and is NOT among the
   units a settings change restarts. Measured: the process started 18:12:57 and
   the graph restarted 21:02:00.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import midi_sync, midi_sync_settings


def _jack_state(period, periods, rate=48000, requested=None):
    text = (f"started=1788310921 device=hw:0 card=KA1 tier=selected audible=yes\n"
            f"period={period}\nrequested_period={requested or period}\n"
            f"periods={periods}\nrate={rate}\n")
    fh = tempfile.NamedTemporaryFile("w", suffix=".state", delete=False)
    fh.write(text)
    fh.close()
    return Path(fh.name)


class RunningGraphIsTheSourceTests(unittest.TestCase):
    def test_latency_uses_the_period_the_server_is_running(self):
        path = _jack_state(96, 2)
        try:
            with mock.patch("patch_browser.audio_engine.JACK_STATE_FILE", path), \
                 mock.patch.dict(os.environ, {"MPE_JACK_BUFFER": "1024",
                                              "MPE_JACK_PERIODS": "3"}, clear=False):
                self.assertAlmostEqual(midi_sync.buffer_latency_ms(), 4.0, places=3)
        finally:
            path.unlink()

    def test_a_fallback_climb_is_compensated_for_the_period_that_won(self):
        """64 requested, 256 running: compensate 256. The old code read the
        request and was 4x wrong, late, exactly when the DAC was already
        struggling."""
        path = _jack_state(256, 2, requested=64)
        try:
            with mock.patch("patch_browser.audio_engine.JACK_STATE_FILE", path), \
                 mock.patch.dict(os.environ, {"MPE_JACK_BUFFER": "64",
                                              "MPE_JACK_PERIODS": "2"}, clear=False):
                self.assertAlmostEqual(midi_sync.buffer_latency_ms(), 256 * 2 / 48.0, places=3)
        finally:
            path.unlink()

    def test_negative_control_no_jack_state_falls_back_to_the_env(self):
        """Without this, a function that always returned a constant would pass."""
        missing = Path(tempfile.gettempdir()) / "definitely-no-such-jack.state"
        with mock.patch("patch_browser.audio_engine.JACK_STATE_FILE", missing), \
             mock.patch.dict(os.environ, {"MPE_JACK_BUFFER": "128",
                                          "MPE_JACK_PERIODS": "2",
                                          "MPE_SURGE_SAMPLE_RATE": "48000"}, clear=False):
            self.assertAlmostEqual(midi_sync.buffer_latency_ms(), 128 * 2 / 48.0, places=3)

    def test_the_legacy_surge_key_is_never_the_graph_period(self):
        """MPE_SURGE_BUFFER_SIZE=1024 must not produce a 1024-frame latency when
        the graph is running 96."""
        path = _jack_state(96, 2)
        try:
            with mock.patch("patch_browser.audio_engine.JACK_STATE_FILE", path), \
                 mock.patch.dict(os.environ, {"MPE_SURGE_BUFFER_SIZE": "1024"}, clear=False):
                self.assertLess(midi_sync.buffer_latency_ms(), 10.0)
        finally:
            path.unlink()

    def test_explicit_arguments_still_win(self):
        """Callers that pass values are asking a hypothetical, not reading the rig."""
        path = _jack_state(96, 2)
        try:
            with mock.patch("patch_browser.audio_engine.JACK_STATE_FILE", path):
                self.assertAlmostEqual(
                    midi_sync.buffer_latency_ms(512, 48000, 3), 512 * 3 / 48.0, places=3)
        finally:
            path.unlink()


class DisplayAndRuntimeAgreeTests(unittest.TestCase):
    """The bug was two computations of one quantity. There is now one."""

    def test_the_displayed_offset_is_the_applied_offset(self):
        path = _jack_state(96, 2)
        try:
            with mock.patch("patch_browser.audio_engine.JACK_STATE_FILE", path), \
                 mock.patch.dict(os.environ, {"MPE_SURGE_BUFFER_SIZE": "1024",
                                              "MPE_JACK_BUFFER": "96",
                                              "MPE_MIDI_OUTPUT_OFFSET_MS": ""}, clear=False), \
                 mock.patch.object(midi_sync_settings, "current_offset_auto", return_value=True):
                applied = midi_sync.resolve_output_offset_ms()
                shown = midi_sync_settings.offset_ms_value()
                self.assertEqual(shown, f"{applied:+.0f} ms")
                self.assertEqual(shown, "-4 ms")
        finally:
            path.unlink()

    def test_the_display_does_not_reimplement_the_calculation(self):
        import inspect
        src = inspect.getsource(midi_sync_settings.offset_ms_value)
        self.assertIn("resolve_output_offset_ms", src)
        self.assertNotIn("current_buffer_size", src.split('"""')[-1],
                         "the display derives the offset again instead of asking for it")


class OffsetTracksTheGraphTests(unittest.TestCase):
    """Frozen at process start is how a buffer change silently stopped applying."""

    def test_the_router_refreshes_the_offset_on_a_graph_change(self):
        src = (Path(__file__).resolve().parent.parent
               / "scripts" / "mpe-pressure-remap.py").read_text(encoding="utf-8")
        self.assertIn("def _refresh_offset", src)
        self.assertIn("self._refresh_offset()", src)

    def test_the_refresh_is_guarded_by_mtime_not_run_every_message(self):
        """A recompute per message would read a file in the MIDI hot path."""
        src = (Path(__file__).resolve().parent.parent
               / "scripts" / "mpe-pressure-remap.py").read_text(encoding="utf-8")
        body = src[src.index("def _refresh_offset"):src.index("def _refresh_clock")]
        self.assertIn("_jack_state_mtime", body)
        self.assertIn("st_mtime <= self._jack_state_mtime", body)

    def test_the_refresh_does_not_fork(self):
        """CPU doctrine: no subprocesses in a periodic loop on the appliance."""
        src = (Path(__file__).resolve().parent.parent
               / "scripts" / "mpe-pressure-remap.py").read_text(encoding="utf-8")
        body = src[src.index("def _refresh_offset"):src.index("def _refresh_clock")]
        for spawn in ("subprocess", "os.system", "os.popen", "Popen"):
            self.assertNotIn(spawn, body)

    def test_a_change_is_logged_with_both_numbers(self):
        """Latency the player did not choose is latency they cannot account for."""
        src = (Path(__file__).resolve().parent.parent
               / "scripts" / "mpe-pressure-remap.py").read_text(encoding="utf-8")
        body = src[src.index("def _refresh_offset"):src.index("def _refresh_clock")]
        self.assertIn("previous", body)
        self.assertIn("MIDI output offset", body)


if __name__ == "__main__":
    unittest.main()
