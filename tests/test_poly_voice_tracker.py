"""Note ledger, the shed policy, and the governor/remapper shed hand-off.

The measured failure (docs/measurements/capacity-fx-96x2-pi5-2026-10-12.md):
the governor lowered Surge's limit 48 -> 26 -> 3 on Crystals and the load stayed
at 70, because a lower limit does not stop sounding voices. Shedding releases
the oldest notes when a step-down lands below the sounding count.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.poly_voice_tracker import (
    SHED_MIN_AGE_S,
    SHED_NOTE_OFF_VELOCITY,
    ActiveNote,
    PolyVoiceTracker,
    fade_actuation_enabled,
    plan_shed,
    read_active_voice_count,
    read_last_note_on_at,
    shed_enabled,
)
from patch_browser.surge_poly_governor import SurgePolyGovernor

REPO = Path(__file__).resolve().parent.parent


def _on(ch: int, note: int) -> list[int]:
    return [0x90 | ch, note, 100]


def _off(ch: int, note: int) -> list[int]:
    return [0x80 | ch, note, 0]


def _pedal(ch: int, down: bool) -> list[int]:
    return [0xB0 | ch, 64, 127 if down else 0]


def _ledger(*starts: tuple[int, int, float]) -> PolyVoiceTracker:
    tracker = PolyVoiceTracker()
    for ch, note, at in starts:
        tracker.observe_message(_on(ch, note), now=at)
    return tracker


class LedgerTests(unittest.TestCase):
    def test_note_on_off_updates_count(self) -> None:
        tracker = PolyVoiceTracker()
        tracker.observe_message(_on(0, 60), now=1.0)
        self.assertEqual(tracker.active_count(), 1)
        tracker.observe_message(_off(0, 60), now=2.0)
        self.assertEqual(tracker.active_count(), 0)

    def test_velocity_zero_note_on_is_a_note_off(self) -> None:
        tracker = _ledger((1, 60, 1.0))
        tracker.observe_message([0x91, 60, 0], now=2.0)
        self.assertEqual(tracker.active_count(), 0)

    def test_persist_carries_count_and_last_note_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "poly-voice-tracker.json"
            tracker = _ledger((1, 64, 5.0), (2, 67, 7.5))
            with mock.patch("patch_browser.poly_voice_tracker.VOICE_TRACKER_FILE", path):
                tracker.persist()
                self.assertEqual(read_active_voice_count(), 2)
                self.assertEqual(read_last_note_on_at(), 7.5)

    def test_last_note_on_survives_the_note_ending(self) -> None:
        """The emergency rule needs the note-on time even for a short note."""
        tracker = _ledger((1, 64, 5.0))
        tracker.observe_message(_off(1, 64), now=5.05)
        self.assertEqual(tracker.snapshot()["last_note_on_at"], 5.0)
        self.assertEqual(tracker.active_count(), 0)


class SustainLedgerTests(unittest.TestCase):
    def test_note_off_under_manager_pedal_keeps_the_note_sounding(self) -> None:
        tracker = _ledger((1, 60, 1.0))
        tracker.observe_message(_pedal(0, True), now=1.5)
        tracker.observe_message(_off(1, 60), now=2.0)
        self.assertEqual(tracker.active_count(), 1, "a sustained note still sounds")
        tracker.observe_message(_pedal(0, False), now=3.0)
        self.assertEqual(tracker.active_count(), 0, "pedal up releases it")

    def test_member_pedal_holds_only_its_own_channel(self) -> None:
        tracker = _ledger((1, 60, 1.0), (2, 62, 1.0))
        tracker.observe_message(_pedal(1, True), now=1.5)
        tracker.observe_message(_off(1, 60), now=2.0)
        tracker.observe_message(_off(2, 62), now=2.0)
        self.assertEqual(tracker.active_count(), 1)

    def test_member_pedal_up_leaves_notes_the_manager_pedal_holds(self) -> None:
        tracker = _ledger((1, 60, 1.0))
        tracker.observe_message(_pedal(0, True), now=1.1)
        tracker.observe_message(_pedal(1, True), now=1.2)
        tracker.observe_message(_off(1, 60), now=2.0)
        tracker.observe_message(_pedal(1, False), now=3.0)
        self.assertEqual(tracker.active_count(), 1)
        tracker.observe_message(_pedal(0, False), now=4.0)
        self.assertEqual(tracker.active_count(), 0)


class PlanShedTests(unittest.TestCase):
    """plan_shed is the policy; these pin which notes go and which never do."""

    def test_oldest_first_and_only_the_excess(self) -> None:
        tracker = _ledger((3, 64, 3.0), (1, 60, 1.0), (4, 65, 4.0), (2, 62, 2.0))
        plan = tracker.plan_shed(limit=2, requested_at=10.0, now=10.0)
        self.assertEqual(plan.release, ((1, 60), (2, 62)))
        self.assertTrue(plan.done)

    def test_nothing_when_at_or_under_the_limit(self) -> None:
        tracker = _ledger((1, 60, 1.0), (2, 62, 2.0))
        plan = tracker.plan_shed(limit=2, requested_at=10.0, now=10.0)
        self.assertEqual(plan.release, ())
        self.assertTrue(plan.done)

    def test_a_note_younger_than_100_ms_is_never_shed(self) -> None:
        self.assertAlmostEqual(SHED_MIN_AGE_S, 0.1)
        notes = [ActiveNote(1, 60, 9.95), ActiveNote(2, 62, 9.96)]
        plan = plan_shed(notes, limit=1, requested_at=10.0, now=10.0)
        self.assertEqual(plan.release, ())
        self.assertEqual(plan.too_young, 2)
        self.assertFalse(plan.done, "a young note can age into eligibility: retry")

    def test_young_notes_age_in_and_the_oldest_goes(self) -> None:
        notes = [ActiveNote(2, 62, 9.96), ActiveNote(1, 60, 9.95)]
        plan = plan_shed(notes, limit=1, requested_at=10.0, now=10.06)
        self.assertEqual(plan.release, ((1, 60),))
        self.assertTrue(plan.done)

    def test_protection_boundary_is_exactly_min_age(self) -> None:
        notes = [ActiveNote(1, 60, 0.0)]
        self.assertEqual(plan_shed(notes, limit=0, requested_at=0.0, now=SHED_MIN_AGE_S).release,
                         ((1, 60),))
        self.assertEqual(plan_shed(notes, limit=0, requested_at=0.0, now=SHED_MIN_AGE_S - 1e-3).release,
                         ())

    def test_old_note_goes_while_a_young_one_is_kept(self) -> None:
        notes = [ActiveNote(1, 60, 5.0), ActiveNote(2, 62, 9.99), ActiveNote(3, 64, 6.0)]
        plan = plan_shed(notes, limit=1, requested_at=10.0, now=10.0)
        self.assertEqual(plan.release, ((1, 60), (3, 64)))
        self.assertTrue(plan.done)

    def test_notes_started_after_the_request_are_not_its_business(self) -> None:
        notes = [ActiveNote(1, 60, 11.0), ActiveNote(2, 62, 12.0)]
        plan = plan_shed(notes, limit=0, requested_at=10.0, now=20.0)
        self.assertEqual(plan.release, ())
        self.assertTrue(plan.done)

    def test_manager_pedal_blocks_every_shed(self) -> None:
        tracker = _ledger((1, 60, 1.0), (2, 62, 2.0), (3, 64, 3.0))
        tracker.observe_message(_pedal(0, True), now=4.0)
        plan = tracker.plan_shed(limit=1, requested_at=10.0, now=10.0)
        self.assertEqual(plan.release, ())
        self.assertEqual(plan.blocked_by_sustain, 3)
        self.assertTrue(plan.done, "nothing to wait for: a pedal is not an age")

    def test_a_held_key_under_its_own_channel_pedal_is_not_shed(self) -> None:
        """A note-off there would only move the voice into Surge's hold buffer."""
        tracker = _ledger((1, 60, 1.0), (2, 62, 2.0))
        tracker.observe_message(_pedal(1, True), now=3.0)
        plan = tracker.plan_shed(limit=0, requested_at=10.0, now=10.0)
        self.assertEqual(plan.release, ((2, 62),))
        self.assertEqual(plan.blocked_by_sustain, 1)

    def test_sustained_notes_are_skipped_and_held_keys_on_free_channels_go(self) -> None:
        tracker = _ledger((1, 60, 1.0), (2, 62, 2.0), (3, 64, 3.0))
        tracker.observe_message(_pedal(1, True), now=3.5)
        tracker.observe_message(_off(1, 60), now=4.0)  # sustained on ch 2
        plan = tracker.plan_shed(limit=1, requested_at=10.0, now=10.0)
        self.assertEqual(plan.release, ((2, 62), (3, 64)))
        self.assertEqual(plan.blocked_by_sustain, 1)


class ChannelReuseTests(unittest.TestCase):
    """After a shed the player's key is still down; the channel is reused only
    after its real note-off. Nothing the shed did may leak onto the next note."""

    def test_real_note_off_after_a_shed_is_a_no_op_and_the_reused_channel_is_fresh(self) -> None:
        tracker = _ledger((5, 60, 1.0), (6, 62, 2.0))
        plan = tracker.plan_shed(limit=1, requested_at=3.0, now=3.0)
        self.assertEqual(plan.release, ((5, 60),))
        tracker.observe_message([0x85, 60, SHED_NOTE_OFF_VELOCITY], now=3.0)
        self.assertFalse(tracker.observe_message(_off(5, 60), now=3.5),
                         "the player's own note-off must not change the ledger")
        self.assertEqual(tracker.active_count(), 1)
        tracker.observe_message(_on(5, 64), now=4.0)
        snap = {(n["channel"], n["note"]): n for n in tracker.snapshot()["notes"]}
        self.assertEqual(snap[(5, 64)]["started_at"], 4.0)
        self.assertFalse(snap[(5, 64)]["sustained"])
        self.assertNotIn((5, 60), snap)
        # The new note on the reused channel is newer than the old request and
        # young for a fresh one: neither cuts it. The older note goes instead.
        self.assertEqual(tracker.plan_shed(limit=1, requested_at=3.0, now=4.0).release, ((6, 62),))
        self.assertEqual(tracker.plan_shed(limit=1, requested_at=4.0, now=4.0).release, ((6, 62),))
        self.assertEqual(tracker.plan_shed(limit=0, requested_at=4.0, now=4.0).release, ((6, 62),))


class FlagTests(unittest.TestCase):
    def test_shed_defaults_on_and_one_flag_turns_it_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(shed_enabled())
            self.assertTrue(fade_actuation_enabled())
        with mock.patch.dict(os.environ, {"MPE_POLY_SHED": "0"}, clear=True):
            self.assertFalse(shed_enabled())
        with mock.patch.dict(os.environ, {"MPE_POLY_GOVERNOR_FADE": "0"}, clear=True):
            self.assertFalse(shed_enabled(), "no ledger, nothing to shed from")


def _governor(effective: int = 12) -> tuple[SurgePolyGovernor, mock.Mock]:
    osc = mock.Mock()
    monitor = mock.Mock()
    monitor.check_health.return_value = (True, None)
    governor = SurgePolyGovernor(osc, surge_monitor=monitor)
    governor._effective_poly = effective
    governor._ceiling_poly = 64
    governor._floor_poly = 4
    return governor, osc


class GovernorShedHandOffTests(unittest.TestCase):
    """The governor decides when; the request carries the limit it set."""

    def _apply(self, env: dict, *, reason: str, active: int, ramp: bool):
        governor, osc = _governor()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("patch_browser.surge_poly_governor.read_active_voice_count",
                       return_value=active),
            mock.patch("patch_browser.surge_poly_governor.write_shed_request") as write,
            mock.patch("patch_browser.surge_poly_governor.send_polylimit",
                       return_value=True) as send,
            mock.patch("builtins.print"),
        ):
            governor._apply_limit(9, reason=reason, cpu=60.0, raw_cpu=60.0,
                                  held_s=0.0, ramp_apply=ramp)
        return governor, write, send

    def test_step_down_below_sounding_sheds_for_every_reason(self) -> None:
        for reason in ("high", "emergency", "warm", "spike"):
            for ramp in (False, True):
                with self.subTest(reason=reason, ramp=ramp):
                    governor, write, send = self._apply({}, reason=reason, active=12, ramp=ramp)
                    write.assert_called_once_with(limit=9, reason=reason)
                    self.assertEqual(send.call_args.args[1], 9)
                    self.assertIsNone(governor._pending_limit)

    def test_no_shed_when_the_limit_is_above_the_sounding_count(self) -> None:
        governor, write, send = self._apply({}, reason="high", active=9, ramp=False)
        write.assert_not_called()
        self.assertEqual(send.call_args.args[1], 9)

    def test_flag_off_never_sheds_and_keeps_the_old_deferral(self) -> None:
        governor, write, send = self._apply({"MPE_POLY_SHED": "0"}, reason="high", active=12, ramp=False)
        write.assert_not_called()
        send.assert_not_called()
        self.assertEqual(governor._pending_limit, 9)

    def test_flag_off_emergency_applies_without_shedding(self) -> None:
        governor, write, send = self._apply({"MPE_POLY_SHED": "0"}, reason="emergency", active=12, ramp=False)
        write.assert_not_called()
        self.assertEqual(send.call_args.args[1], 9)

    def test_step_up_never_sheds(self) -> None:
        governor, osc = _governor(effective=6)
        with (
            mock.patch("patch_browser.surge_poly_governor.read_active_voice_count", return_value=12),
            mock.patch("patch_browser.surge_poly_governor.write_shed_request") as write,
            mock.patch("patch_browser.surge_poly_governor.send_polylimit", return_value=True),
            mock.patch("builtins.print"),
        ):
            governor._apply_limit(7, reason="recover", cpu=40.0, raw_cpu=40.0, held_s=5.0)
        write.assert_not_called()


def _load_remap_module():
    spec = importlib.util.spec_from_file_location(
        "mpe_pressure_remap_shed", REPO / "scripts" / "mpe-pressure-remap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RemapperShedTests(unittest.TestCase):
    """The remapper is the one MIDI sender; this drives its real request path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.request = tmp / "governor-fade-request.json"
        self.ledger = tmp / "poly-voice-tracker.json"
        self.module = _load_remap_module()
        self.patches = [
            mock.patch("patch_browser.poly_voice_tracker.FADE_REQUEST_FILE", self.request),
            mock.patch("patch_browser.poly_voice_tracker.VOICE_TRACKER_FILE", self.ledger),
            mock.patch.object(self.module, "FADE_REQUEST_FILE", self.request),
            mock.patch("builtins.print"),
            mock.patch.dict(os.environ, {}, clear=True),
        ]
        for patch in self.patches:
            patch.start()
        self.daemon = self.module.PressureRemapDaemon()
        self.sent: list[list[int]] = []
        self.daemon._out = mock.Mock(send_message=lambda msg: self.sent.append(list(msg)))

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self._tmp.cleanup()

    def _play(self, ch: int, note: int, at: float) -> None:
        with mock.patch("patch_browser.poly_voice_tracker.time.monotonic", return_value=at):
            self.daemon._send(_on(ch, note))

    def _request(self, limit: int, at: float) -> None:
        self.request.write_text(json.dumps(
            {"request_id": at, "requested_at": at, "limit": limit, "reason": "high"}))

    def test_releases_the_oldest_with_a_plain_note_off_and_clears_the_request(self) -> None:
        for i, at in enumerate((1.0, 2.0, 3.0, 4.0)):
            self._play(1 + i, 60 + i, at)
        self.sent.clear()
        self._request(limit=2, at=5.0)
        self.daemon._process_shed_request(5.0)
        self.assertEqual(self.sent, [[0x81, 60, 64], [0x82, 61, 64]],
                         "a plain note-off at release velocity 64, oldest first")
        self.assertEqual(self.daemon._voice_tracker.active_count(), 2)
        self.assertFalse(self.request.exists(), "a finished request must be cleared")
        self.assertEqual(read_active_voice_count(), 2, "the governor's view must follow")

    def test_a_young_note_is_retried_once_it_ages(self) -> None:
        self._play(1, 60, 4.95)
        self._play(2, 62, 4.97)
        self.sent.clear()
        self._request(limit=1, at=5.0)
        self.daemon._process_shed_request(5.0)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.request.exists())
        self.daemon._process_shed_request(5.06)
        self.assertEqual(self.sent, [[0x81, 60, SHED_NOTE_OFF_VELOCITY]])
        self.assertFalse(self.request.exists())

    def test_the_same_request_is_executed_once(self) -> None:
        for i, at in enumerate((1.0, 2.0, 3.0)):
            self._play(1 + i, 60 + i, at)
        self._request(limit=2, at=5.0)
        self.daemon._process_shed_request(5.0)
        self.sent.clear()
        self._play(4, 70, 5.5)
        self.sent.clear()
        self._request(limit=2, at=5.0)  # rewritten, same id
        self.daemon._process_shed_request(6.0)
        self.assertEqual(self.sent, [])

    def test_sustain_blocks_and_nothing_is_sent(self) -> None:
        self._play(1, 60, 1.0)
        self._play(2, 62, 2.0)
        self.daemon._send(_pedal(0, True))
        self.sent.clear()
        self._request(limit=0, at=5.0)
        self.daemon._process_shed_request(5.0)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.request.exists())


if __name__ == "__main__":
    unittest.main()
