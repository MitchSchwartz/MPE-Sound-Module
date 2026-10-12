"""The governor against the 2026-10-12 capacity measurement (96 x 2, Pi 5).

docs/measurements/capacity-fx-96x2-pi5-2026-10-12.md. Two findings drive this:
the always_on curve reached its floor at raw 90 while overruns began at 51-55,
and one note-on overrun at raw 15 sent the limit 48 -> 3. The tests replay the
measured loads through the real governor tick, with the meter, the clock, the
note ledger and Surge's OSC replaced by a simulation.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from patch_browser.governor_v2 import (
    ATTACK_WINDOW_S,
    EVENT_MERGE_S,
    REPEAT_WINDOW_S,
    always_on_target_limit,
    xrun_at_note_on,
    xrun_warrants_emergency,
)
from patch_browser.surge_poly_governor import (
    DEFAULT_ALWAYS_ON_LIMIT_HARD,
    DEFAULT_ALWAYS_ON_REST_CAP,
    DEFAULT_JACK_BASELINE,
    SurgePolyGovernor,
    load_governor_config,
)

# dsp_percent medians from the measurement's per-window table, by held notes.
# Per note between and beyond the measured points: Crystals ~3.7, Cloud Horn ~2.1.
CRYSTALS = {4: 26.6, 10: 48.4, 11: 51.9, 12: 55.6}
CLOUD_HORN = {4: 21.2, 18: 49.1, 19: 51.0, 20: 53.6}
PER_NOTE = {"crystals": 3.7, "cloud_horn": 2.1}
IDLE_THIS_RUN = (7.0, 11.1, 11.5, 13.0, 13.2)
IDLE_154 = (17.63, 26.90, 30.40, 35.19, 37.99)
# Loads at which the measurement saw no overrun at all.
CLEAN = {"crystals": (10, (48.2, 48.5, 48.4)), "cloud_horn": (18, (48.5, 49.0))}
# Loads at which every window overran: the governor must be below that count.
EVERY_WINDOW_OVERRAN = {"crystals": (12, (55.4, 55.7)), "cloud_horn": (20, (53.1, 54.1))}

PI5_ENV = {
    "MPE_POLY_GOVERNOR_V2": "1",
    "MPE_POLY_GOVERNOR_METER": "jack",
    "MPE_POLY_LIMIT_MODE": "always_on",
    "MPE_POLY_MIN_HEADROOM": "3",
    "MPE_POLY_STEP_UP": "1",
    "MPE_POLY_EMERGENCY_XRUN_ONLY": "1",
    "MPE_POLY_RISE_ENABLE": "1",
    "MPE_POLY_RAMP_APPLY": "1",
    "MPE_POLY_FLOOR": "4",
}
OLD_CONSTANTS = {"MPE_POLY_JACK_BASELINE": "40", "MPE_POLY_LIMIT_HARD": "50", "MPE_POLY_REST_CAP": "48"}


def load_for(patch: str, notes: int) -> float:
    table = CRYSTALS if patch == "crystals" else CLOUD_HORN
    if notes <= 0:
        return 11.5
    points = sorted(table)
    if notes in table:
        return table[notes]
    if notes > points[-1]:
        return table[points[-1]] + PER_NOTE[patch] * (notes - points[-1])
    if notes < points[0]:
        return table[points[0]] - PER_NOTE[patch] * (points[0] - notes)
    lo = max(p for p in points if p < notes)
    hi = min(p for p in points if p > notes)
    return table[lo] + (table[hi] - table[lo]) * (notes - lo) / (hi - lo)


class Sim:
    """Surge, the meter, the ledger and the clock, as the governor sees them."""

    def __init__(self, patch: str, held: int, *, on_load: int = 61) -> None:
        self.patch = patch
        self.notes = held
        self.now = 1000.0
        self.limits: list[int] = []
        self.sheds: list[tuple[int, int]] = []
        self.xrun_total = 0
        self.pending_xruns = 0
        self.last_note_on_at: float | None = None
        self.on_load = on_load

    def raw(self) -> float:
        return load_for(self.patch, self.notes)

    def shed(self, *, limit: int, reason: str) -> None:
        # Steady holds: every note is older than the 100 ms protection.
        before = self.notes
        self.notes = min(self.notes, limit)
        self.sheds.append((before, self.notes))

    def send(self, _client, limit: int) -> bool:
        self.limits.append(limit)
        return True

    def xruns(self) -> tuple[int, int]:
        delta, self.pending_xruns = self.pending_xruns, 0
        self.xrun_total += delta
        return self.xrun_total, delta


class GovernorHarness:
    def __init__(self, sim: Sim, env: dict[str, str]) -> None:
        self.sim = sim
        self.env = env

    def __enter__(self) -> SurgePolyGovernor:
        self._stack = ExitStack()
        stack = self._stack
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        state = tmp / "poly.json"
        self.state = state
        state.write_text(json.dumps({
            "patch": self.sim.patch, "native_poly": 64, "ceiling_poly": 64,
            "effective_poly": self.sim.on_load, "reuse_single": True}))
        stripped = {k: v for k, v in os.environ.items() if not k.startswith("MPE_POLY_")}
        stack.enter_context(mock.patch.dict(os.environ, {**stripped, **PI5_ENV, **self.env}, clear=True))
        sim = self.sim
        stack.enter_context(mock.patch("time.monotonic", lambda: sim.now))
        for target in ("patch_browser.surge_playback.POLY_STATE_FILE",
                       "patch_browser.surge_poly_governor.POLY_STATE_FILE"):
            stack.enter_context(mock.patch(target, state))
        g = "patch_browser.surge_poly_governor."
        stack.enter_context(mock.patch(g + "governor_active", return_value=True))
        stack.enter_context(mock.patch(g + "send_polylimit", side_effect=sim.send))
        stack.enter_context(mock.patch(g + "read_active_voice_count", side_effect=lambda: sim.notes))
        stack.enter_context(mock.patch(g + "write_shed_request", side_effect=sim.shed))
        stack.enter_context(mock.patch(g + "read_last_note_on_at", side_effect=lambda: sim.last_note_on_at))
        self.printed = stack.enter_context(mock.patch("builtins.print"))
        monitor = mock.Mock()
        monitor.check_health.return_value = (True, None)
        governor = SurgePolyGovernor(mock.Mock(), surge_monitor=monitor)
        tracker = governor._load_tracker
        stack.enter_context(mock.patch.object(tracker, "_read_meter_dsp",
                                              side_effect=lambda _now: (sim.raw(), sim.raw())))
        stack.enter_context(mock.patch.object(tracker, "_read_xruns", side_effect=sim.xruns))
        governor._refresh_patch_state()
        self.governor = governor
        return governor

    def run(self, seconds: float) -> None:
        end = self.sim.now + seconds
        while self.sim.now < end:
            self.governor._tick_v2_always_on()
            self.sim.now += self.governor._adaptive_poll_interval

    def tick(self) -> None:
        self.governor._tick_v2_always_on()

    def __exit__(self, *exc) -> None:
        self._stack.close()


def settle(patch: str, held: int, env: dict[str, str] | None = None, seconds: float = 40.0) -> Sim:
    sim = Sim(patch, held)
    harness = GovernorHarness(sim, env or {})
    with harness:
        harness.run(seconds)
    return sim


class ThresholdTests(unittest.TestCase):
    def test_constants_are_the_derived_ones(self) -> None:
        self.assertEqual((DEFAULT_JACK_BASELINE, DEFAULT_ALWAYS_ON_LIMIT_HARD, DEFAULT_ALWAYS_ON_REST_CAP),
                         (40.0, 15.0, 18))
        with mock.patch.dict(os.environ, {"MPE_POLY_LIMIT_MODE": "always_on"}, clear=True):
            cfg = load_governor_config()
        self.assertEqual((cfg.jack_baseline, cfg.limit_hard, cfg.rest_cap), (40.0, 15.0, 18))
        with mock.patch.dict(os.environ, {"MPE_POLY_LIMIT_MODE": "progressive"}, clear=True):
            self.assertEqual(load_governor_config().rest_cap, 0, "rest cap is an always_on knob")

    def test_idle_never_steps_down(self) -> None:
        """Both idle datasets: this run's meter (7-13) and #154's jackd figure (17-38)."""
        for raw in IDLE_THIS_RUN + IDLE_154:
            with self.subTest(raw=raw):
                sim = Sim("crystals", 0, on_load=18)
                harness = GovernorHarness(sim, {})
                with harness, mock.patch.object(Sim, "raw", lambda _self, r=raw: r):
                    harness.run(10.0)
                self.assertEqual(sim.limits, [], f"stepped at idle {raw}")

    def test_clean_loads_do_not_step_down(self) -> None:
        for patch, (notes, loads) in CLEAN.items():
            for raw in loads:
                with self.subTest(patch=patch, raw=raw):
                    sim = Sim(patch, notes, on_load=18)
                    harness = GovernorHarness(sim, {})
                    with harness, mock.patch.object(Sim, "raw", lambda _self, r=raw: r):
                        harness.run(5.0)
                    self.assertEqual(sim.limits, [], f"{patch} cut at a clean {raw}")
                    self.assertEqual(sim.notes, notes)

    def test_curve_is_below_the_overrunning_count_where_every_window_overran(self) -> None:
        for patch, (notes, loads) in EVERY_WINDOW_OVERRAN.items():
            for raw in loads:
                with self.subTest(patch=patch, raw=raw):
                    target = always_on_target_limit(
                        raw - DEFAULT_JACK_BASELINE, ceiling=64, floor=4, min_headroom=3,
                        hard=DEFAULT_ALWAYS_ON_LIMIT_HARD, rest_cap=DEFAULT_ALWAYS_ON_REST_CAP)
                    self.assertLess(target, notes - 1)

    def test_held_crystals_chord_settles_at_its_clean_ceiling(self) -> None:
        for held in (12, 14, 16):
            with self.subTest(held=held):
                sim = settle("crystals", held)
                self.assertEqual(sim.notes, 10, "measured clean: 10 (b), 11 (a); 12 overran")

    def test_held_cloud_horn_settles_at_its_clean_ceiling(self) -> None:
        for held in (19, 20, 23):
            with self.subTest(held=held):
                sim = settle("cloud_horn", held)
                self.assertIn(sim.notes, (17, 18), "measured: 18 rare, 19 overran")

    def test_load_stays_out_of_the_overrun_band_after_settling(self) -> None:
        for patch, held in (("crystals", 16), ("cloud_horn", 23)):
            with self.subTest(patch=patch):
                sim = settle(patch, held)
                self.assertLess(sim.raw(), 50.8, "first overrunning median was 50.8")

    def test_light_patch_is_not_cut(self) -> None:
        """Closed Hat-like: load flat near idle at any count; only the rest cap applies."""
        sim = Sim("crystals", 30)
        harness = GovernorHarness(sim, {})
        with harness, mock.patch.object(Sim, "raw", lambda _self: 10.5):
            harness.run(20.0)
        self.assertEqual(sim.notes, 18)
        self.assertEqual(min(sim.limits), 18)

    def test_old_constants_never_cut_the_overrunning_chord(self) -> None:
        """Positive control for the closed loop: 40 / 50 / 48 leaves Crystals at 12."""
        sim = settle("crystals", 12, env=OLD_CONSTANTS)
        self.assertEqual(sim.notes, 12)


class EmergencyRuleTests(unittest.TestCase):
    """xrun_warrants_emergency is the rule; one row per measured case."""

    def test_rule(self) -> None:
        hard = DEFAULT_ALWAYS_ON_LIMIT_HARD
        cases = [
            # (stress, at_note_on, prior gaps before now, expected, why)
            (0.0, True, (), False, "Cloud Horn: one note-on overrun at raw 15.6"),
            (5.0, False, (), False, "isolated, low load, no note-on"),
            (5.0, True, (3.0,), False, "note-on overrun with an earlier event: still attack"),
            (0.0, True, (3.0,), False, "note-on overrun at idle-level load after an isolated one"),
            (5.0, False, (3.0,), True, "a second non-note-on event within 10 s"),
            (5.0, False, (REPEAT_WINDOW_S,), True, "repeat window is inclusive"),
            (5.0, False, (REPEAT_WINDOW_S + 0.01,), False, "outside the repeat window"),
            (5.0, False, (0.2,), False, "same event across two meter writes"),
            (5.0, False, (EVENT_MERGE_S + 0.01,), True, "past the merge gap is a repeat"),
            (52.4, True, (), True, "Crystals: overrun at raw 92.4, at a note-on"),
            (hard, False, (), True, "at the floor point, one overrun is enough"),
            (hard - 0.01, True, (), False, "just under the floor point, at note-on"),
        ]
        now = 100.0
        for stress, at_note_on, gaps, expected, why in cases:
            with self.subTest(why=why):
                self.assertEqual(
                    xrun_warrants_emergency(stress=stress, hard=hard, at_note_on=at_note_on,
                                            prior_events=[now - g for g in gaps], now=now),
                    expected)

    def test_note_on_window(self) -> None:
        self.assertTrue(xrun_at_note_on(now=10.0, last_note_on_at=9.95))
        self.assertTrue(xrun_at_note_on(now=10.0, last_note_on_at=10.0 - ATTACK_WINDOW_S))
        self.assertFalse(xrun_at_note_on(now=10.0, last_note_on_at=10.0 - ATTACK_WINDOW_S - 0.01))
        self.assertFalse(xrun_at_note_on(now=10.0, last_note_on_at=None))
        self.assertFalse(xrun_at_note_on(now=10.0, last_note_on_at=10.5), "a later note-on")


class EmergencyInGovernorTests(unittest.TestCase):
    """The rule wired into the real tick: what the limit does, and what is logged."""

    def _replay(self, events, *, note_on_lag: float | None, patch: str = "cloud_horn",
                notes: int = 6, raw: float | None = None):
        """events: (seconds from start, raw). A note-on precedes each by note_on_lag."""
        sim = Sim(patch, notes, on_load=18)
        harness = GovernorHarness(sim, {})
        start = sim.now
        with harness:
            if raw is not None:
                patcher = mock.patch.object(Sim, "raw", lambda _self: raw)
                patcher.start()
            try:
                for at, event_raw in events:
                    sim.now = start + at
                    if note_on_lag is not None:
                        sim.last_note_on_at = sim.now - note_on_lag
                    sim.pending_xruns = 3  # one event, three client notifications
                    with mock.patch.object(Sim, "raw", lambda _self, r=event_raw: r):
                        harness.tick()
            finally:
                if raw is not None:
                    patcher.stop()
            counts = dict(harness.governor._xrun_counts)
            lines = [str(c.args[0]) for c in harness.printed.call_args_list if c.args]
        return sim, counts, lines

    CLOUD_HORN_CELL = [(38.37, 15.6), (39.38, 17.8), (45.38, 21.1), (46.38, 20.9), (54.38, 25.7)]

    def test_cloud_horn_note_on_overruns_no_longer_drop_to_three(self) -> None:
        sim, counts, lines = self._replay(self.CLOUD_HORN_CELL, note_on_lag=0.05)
        self.assertNotIn(3, sim.limits, "a note-on overrun at low load took the emergency")
        self.assertEqual(counts, {"note_on": 5, "isolated": 0, "emergency": 0})
        kept = [line for line in lines if "xrun kept" in line]
        self.assertEqual(len(kept), 5, "every ignored overrun must be in the journal")
        self.assertTrue(all("kind=note_on" in line for line in kept))

    def test_without_note_on_evidence_the_repeat_arm_fires(self) -> None:
        """No ledger (remapper bypassed, as in the measurement): 38.37 then 39.38
        is a repeat within 10 s. The rule fails toward the emergency, not away."""
        sim, counts, _ = self._replay(self.CLOUD_HORN_CELL, note_on_lag=None)
        self.assertIn(3, sim.limits)
        self.assertEqual(counts["isolated"], 1)
        self.assertGreaterEqual(counts["emergency"], 1)

    def test_note_on_overruns_are_not_priors_for_a_later_one(self) -> None:
        """After the Cloud Horn cell, one overrun away from any note-on is
        isolated: attack events must not count toward the repeat arm."""
        sim = Sim("cloud_horn", 6, on_load=18)
        harness = GovernorHarness(sim, {})
        start = sim.now
        with harness:
            for at, raw in self.CLOUD_HORN_CELL + [(57.0, 24.0)]:
                sim.now = start + at
                sim.last_note_on_at = sim.now - 0.05 if at < 55 else start + 54.33
                sim.pending_xruns = 3
                with mock.patch.object(Sim, "raw", lambda _self, r=raw: r):
                    harness.tick()
            counts = dict(harness.governor._xrun_counts)
        self.assertNotIn(3, sim.limits)
        self.assertEqual(counts, {"note_on": 5, "isolated": 1, "emergency": 0})

    def test_isolated_low_load_overrun_is_counted_not_acted_on(self) -> None:
        sim, counts, lines = self._replay([(5.0, 30.0)], note_on_lag=None)
        self.assertEqual(sim.limits, [])
        self.assertEqual(counts, {"note_on": 0, "isolated": 1, "emergency": 0})
        self.assertTrue(any("xrun kept" in line and "kind=isolated" in line for line in lines))

    def test_repeated_overruns_take_the_emergency(self) -> None:
        sim, counts, _ = self._replay([(5.0, 45.0), (8.0, 45.0)], note_on_lag=None)
        self.assertEqual(sim.limits[-1], 3)
        self.assertEqual(counts["emergency"], 1)

    def test_overrun_at_high_load_takes_the_emergency_even_at_a_note_on(self) -> None:
        sim, counts, _ = self._replay([(5.0, 92.4)], note_on_lag=0.02, patch="crystals", notes=16)
        self.assertEqual(sim.limits[-1], 3)
        self.assertEqual(counts["emergency"], 1)
        self.assertEqual(sim.notes, 3, "the emergency drop sheds down to its limit")

    def test_counts_are_in_the_snapshot(self) -> None:
        sim = Sim("cloud_horn", 6, on_load=18)
        harness = GovernorHarness(sim, {})
        with harness:
            self.assertEqual(harness.governor.snapshot()["xruns"],
                             {"note_on": 0, "isolated": 0, "emergency": 0})


class PatchChangeTests(unittest.TestCase):
    def test_every_patch_load_is_pulled_to_the_rest_cap_at_once(self) -> None:
        """A patch load writes its on-load limit (here 61); the governor must not
        spend 43 steps of 0.25 s walking it down under load."""
        sim = Sim("crystals", 0, on_load=61)
        harness = GovernorHarness(sim, {})
        with harness, mock.patch.object(Sim, "raw", lambda _self: 11.0):
            harness.tick()
            self.assertEqual(sim.limits, [18])
            harness.state.write_text(json.dumps({
                "patch": "Other", "native_poly": 64, "ceiling_poly": 64,
                "effective_poly": 61, "reuse_single": True}))
            stamp = harness.state.stat().st_mtime + 5
            os.utime(harness.state, (stamp, stamp))
            harness.tick()
            self.assertEqual(sim.limits, [18, 18])

    def test_an_external_limit_write_without_a_patch_change_is_pulled_down(self) -> None:
        """The capacity run wrote effective 48 into the state file mid-session."""
        sim = Sim("crystals", 0, on_load=18)
        harness = GovernorHarness(sim, {})
        with harness, mock.patch.object(Sim, "raw", lambda _self: 11.0):
            harness.tick()
            self.assertEqual(sim.limits, [])
            state = json.loads(harness.state.read_text())
            state["effective_poly"] = 48
            harness.state.write_text(json.dumps(state))
            stamp = harness.state.stat().st_mtime + 5
            os.utime(harness.state, (stamp, stamp))
            sim.now += 30.0
            harness.tick()
            self.assertEqual(sim.limits, [18])


if __name__ == "__main__":
    unittest.main()
