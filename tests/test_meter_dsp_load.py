"""The governor's load reading, from jack_cpu_load() to a poly decision (#154).

jack_cpu_load() already returns a percentage, 0-100 of the period. From
2026-08-23 (d4d91cb) the meter multiplied it by 100 and clamped to 100, so
dsp_percent read 100.000 at every real load of 1% or more, and the governor's
baseline (97) was tuned against that pinned number. These tests run the real
meter source against the fake libjack, read the meter.state it writes, and feed
that file to the governor's real reader.

Pinned value: on 2026-10-11 jackd's own fCPULoad read 30.40 while meter.state
said 100.000 (#154, independent shm read). The meter must now publish 30.400.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from patch_browser.governor_load import LoadTracker
from patch_browser.governor_v2 import always_on_rest_top
from patch_browser.surge_poly_governor import (
    DEFAULT_ALWAYS_ON_LIMIT_HARD,
    DEFAULT_JACK_BASELINE,
    SurgePolyGovernor,
    load_governor_config,
)

REPO = Path(__file__).resolve().parents[1]
FAKE_JACK = REPO / "tests" / "fixtures" / "fake_jack"
METER_SRC = REPO / "native" / "mpe-peak-meter" / "mpe-peak-meter.c"

#: Measured idle band at 96 x 2, full stack, 0 voices (#154): jackd 17-38%.
IDLE_READINGS = (17.63, 26.90, 30.40, 35.19, 37.99)


def _build_meter_driver(out: Path) -> None:
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
         f"-I{FAKE_JACK}", f"-I{METER_SRC.parent}", "-DCLIENT_METER",
         f'-DCLIENT_SRC="{METER_SRC}"', str(FAKE_JACK / "driver.c"),
         "-o", str(out), "-lm", "-lpthread"],
        check=True, capture_output=True, text=True, timeout=120)


@unittest.skipUnless(shutil.which("cc"), "no C compiler — meter source not checked")
class MeterPublishesJackLoadTests(unittest.TestCase):
    """What the compiled meter writes for a given jack_cpu_load()."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.driver = cls.tmp / "meter-dsp"
        _build_meter_driver(cls.driver)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _publish(self, jack_value: str) -> tuple[Path, dict[str, str], str]:
        run = Path(tempfile.mkdtemp(dir=self.tmp))
        proc = subprocess.run(
            [str(self.driver)], input=f"cpuload {jack_value}\ndsp\n",
            env={**os.environ, "MPE_RUN_DIR": str(run)},
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(proc.stdout.strip(), "dsp written", "driver skipped the write")
        state_path = run / "meter.state"
        self.assertTrue(state_path.is_file(), "meter wrote no meter.state")
        fields = dict(line.split("=", 1) for line in state_path.read_text().splitlines())
        self.assertIn("dsp_percent", fields, "meter.state lost the dsp_percent line")
        return run, fields, proc.stderr

    def _governor_reads(self, run: Path) -> tuple[float | None, float | None]:
        tracker = LoadTracker(meter_mode="jack", jack_baseline=DEFAULT_JACK_BASELINE)
        with mock.patch("patch_browser.governor_load.run_dir", return_value=run):
            return tracker._read_meter_dsp(time.monotonic())

    # --- scale ------------------------------------------------------------------

    def test_pinned_reading_from_154_is_published_as_is(self):
        run, fields, _ = self._publish("30.40")
        self.assertEqual(fields["dsp_percent"], "30.400")
        load, raw = self._governor_reads(run)
        self.assertAlmostEqual(load, 30.40, places=3)
        self.assertAlmostEqual(raw, 30.40, places=3)

    def test_a_low_load_is_not_scaled_up(self):
        """Under the double scaling, 0.5% became 50.0."""
        _, fields, _ = self._publish("0.5")
        self.assertEqual(fields["dsp_percent"], "0.500")

    def test_the_whole_measured_idle_band_reads_through(self):
        for value in IDLE_READINGS:
            with self.subTest(jack=value):
                run, fields, _ = self._publish(str(value))
                self.assertAlmostEqual(float(fields["dsp_percent"]), value, places=2)
                load, _ = self._governor_reads(run)
                self.assertAlmostEqual(load, value, places=2)

    def test_the_range_ends_are_valid(self):
        for value, text in (("0", "0.000"), ("100", "100.000")):
            with self.subTest(jack=value):
                _, fields, _ = self._publish(value)
                self.assertEqual(fields["dsp_percent"], text)

    # --- out of range -------------------------------------------------------------

    def test_impossible_values_are_published_invalid_not_clamped(self):
        """2690 is what the old scaling made of 26.90. It must not read as 100."""
        for value in ("2690", "100.5", "-1", "nan", "inf"):
            with self.subTest(jack=value):
                run, fields, stderr = self._publish(value)
                self.assertEqual(fields["dsp_percent"], "invalid")
                self.assertIn("outside 0-100", stderr, "invalid reading was not logged")
                self.assertEqual(self._governor_reads(run), (None, None),
                                 "the governor must treat an invalid reading as stale")


class GovernorStalePathTests(unittest.TestCase):
    """An out-of-range dsp_percent takes the governor's existing stale path."""

    def _state(self, run: Path, dsp: str) -> None:
        (run / "meter.state").write_text(
            f"source=jack\nxruns=0\ndsp_percent={dsp}\nupdated={int(time.time())}\n")

    def test_out_of_range_falls_back_to_proc_and_says_so(self):
        for dsp in ("150.000", "-3.000", "nan", "invalid"):
            with self.subTest(dsp=dsp), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp)
                self._state(run, dsp)
                tracker = LoadTracker(meter_mode="jack", jack_baseline=DEFAULT_JACK_BASELINE)
                with (
                    mock.patch("patch_browser.governor_load.run_dir", return_value=run),
                    mock.patch.object(tracker, "_read_proc_cpu", return_value=12.0),
                    mock.patch("builtins.print") as printed,
                ):
                    sample = tracker.sample()
                assert sample is not None
                self.assertEqual(sample.source, "proc")
                self.assertEqual(sample.load, 12.0)
                logged = " ".join(str(c.args[0]) for c in printed.call_args_list)
                self.assertIn("dsp_percent stale", logged)

    def test_positive_control_in_range_reading_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            self._state(run, "30.400")
            tracker = LoadTracker(meter_mode="jack", jack_baseline=DEFAULT_JACK_BASELINE)
            with (
                mock.patch("patch_browser.governor_load.run_dir", return_value=run),
                mock.patch.object(tracker, "_read_proc_cpu", return_value=12.0),
            ):
                sample = tracker.sample()
            assert sample is not None
            self.assertEqual(sample.source, "jack")
            self.assertAlmostEqual(sample.load, 30.4)


PI5_ENV = {
    "MPE_POLY_GOVERNOR_V2": "1",
    "MPE_POLY_GOVERNOR_METER": "jack",
    "MPE_POLY_LIMIT_MODE": "always_on",
    "MPE_POLY_MIN_HEADROOM": "3",
    "MPE_POLY_REST_CAP": "0",
    "MPE_POLY_STEP_UP": "1",
    "MPE_POLY_EMERGENCY_XRUN_ONLY": "1",
    "MPE_POLY_RISE_ENABLE": "1",
}


class GovernorResponseAtBaselineTests(unittest.TestCase):
    """Real 0-100 readings through the shipped defaults: idle is calm, load bites."""

    @contextmanager
    def _env(self):
        stripped = {k: v for k, v in os.environ.items()
                    if k not in ("MPE_POLY_JACK_BASELINE", "MPE_POLY_LIMIT_HARD")}
        with mock.patch.dict(os.environ, {**stripped, **PI5_ENV}, clear=True):
            yield

    def _tick(self, raw: float) -> tuple[list[int], SurgePolyGovernor, object]:
        import json
        sends: list[int] = []
        with tempfile.TemporaryDirectory() as tmp, self._env():
            state_path = Path(tmp) / "poly.json"
            ceiling = 12
            rest_top = always_on_rest_top(ceiling=ceiling, floor=4, min_headroom=3)
            state_path.write_text(json.dumps({
                "patch": "Lead", "native_poly": 16, "ceiling_poly": ceiling,
                "effective_poly": rest_top, "reuse_single": True}))
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(mock.Mock(), surge_monitor=monitor)
            tracker = governor._load_tracker
            with (
                mock.patch("patch_browser.surge_playback.POLY_STATE_FILE", state_path),
                mock.patch("patch_browser.surge_poly_governor.POLY_STATE_FILE", state_path),
                mock.patch("patch_browser.surge_poly_governor.governor_active",
                           return_value=True),
                mock.patch("patch_browser.surge_poly_governor.send_polylimit",
                           side_effect=lambda _c, limit: sends.append(limit) or True),
                mock.patch.object(tracker, "_read_meter_dsp", return_value=(raw, raw)),
                mock.patch.object(tracker, "_read_xruns", return_value=(0, 0)),
                mock.patch("builtins.print"),
            ):
                governor._last_patch = "Lead"
                governor._refresh_patch_state()
                sample = tracker.sample()
                with mock.patch.object(tracker, "sample", return_value=sample):
                    governor._tick_v2_always_on()
        return sends, governor, sample

    def test_shipped_defaults_are_the_derived_ones(self):
        with self._env():
            cfg = load_governor_config()
            monitor = mock.Mock()
            governor = SurgePolyGovernor(mock.Mock(), surge_monitor=monitor)
        self.assertEqual(cfg.jack_baseline, DEFAULT_JACK_BASELINE)
        self.assertEqual(cfg.limit_hard, DEFAULT_ALWAYS_ON_LIMIT_HARD)
        self.assertEqual(governor._load_tracker.jack_baseline(), DEFAULT_JACK_BASELINE,
                         "the tracker must take the baseline from the config, not its own env read")

    def test_idle_is_no_stress(self):
        for raw in IDLE_READINGS:
            with self.subTest(raw=raw):
                sends, governor, sample = self._tick(raw)
                self.assertEqual(sample.normalized_load, 0.0)
                self.assertEqual(sends, [], "the governor stepped the limit at idle")
                self.assertIsNotNone(governor._low_since, "idle did not take the recover branch")

    def test_load_above_threshold_is_stress(self):
        """70% of the period: Surge's measured worst (30%) on top of the idle top."""
        sends, governor, sample = self._tick(70.0)
        self.assertGreater(sample.normalized_load, 9.0)
        self.assertIsNone(governor._low_since)
        self.assertTrue(sends, "the governor did not respond to 70% load")
        self.assertLess(sends[-1], always_on_rest_top(ceiling=12, floor=4, min_headroom=3))

    def test_one_authority_parity_files_do_not_restate_the_constants(self):
        files = sorted((REPO / "config" / "platform").glob("player-env-parity*.env"))
        self.assertTrue(files, "no parity files found — the check would pass vacuously")
        for path in files:
            with self.subTest(file=path.name):
                set_keys = {line.split("=", 1)[0] for line in path.read_text().splitlines()
                            if line and not line.startswith("#") and "=" in line}
                self.assertNotIn("MPE_POLY_JACK_BASELINE", set_keys)
                self.assertNotIn("MPE_POLY_LIMIT_HARD", set_keys)


if __name__ == "__main__":
    unittest.main()
