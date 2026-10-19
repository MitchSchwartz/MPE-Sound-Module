"""Pedal fade: the remapper owns the sustain pedal so pedal-held notes decay.

The measured failure: on organ and pad patches (amp sustain 1.0) a note held
by the pedal never decays, so chords added under the pedal stacked from 3 to
~13 voices and the level climbed ~7 dB. With ``MPE_PEDAL_FADE_S`` > 0 the
remapper keeps CC 64 from Surge and sends each pedal-held note's note-off at
release + fade. All time here is a fake clock.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from patch_browser.midi_sync import is_note_off
from patch_browser.pedal_fade import (
    DEFAULT_PEDAL_FADE_S,
    RELEASE_CURVES,
    PedalFade,
    hold_register,
    register_hold_seconds,
    pedal_fade_seconds,
    release_curve,
    release_fade_seconds,
    release_schedule,
)

REPO = Path(__file__).resolve().parent.parent
FADE = 3.0


def _on(ch: int, note: int, vel: int = 100) -> list[int]:
    return [0x90 | ch, note, vel]


def _off(ch: int, note: int, vel: int = 40) -> list[int]:
    return [0x80 | ch, note, vel]


def _pedal(ch: int, down: bool) -> list[int]:
    return [0xB0 | ch, 64, 127 if down else 0]


def _note_offs(sent: list[list[int]]) -> Counter:
    return Counter((m[0] & 0x0F, m[1]) for m in sent if is_note_off(m))


class EnvTests(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(pedal_fade_seconds(""), DEFAULT_PEDAL_FADE_S)
        self.assertEqual(pedal_fade_seconds("2.5"), 2.5)
        self.assertEqual(pedal_fade_seconds("0"), 0.0)
        self.assertEqual(pedal_fade_seconds("-1"), 0.0)
        self.assertEqual(pedal_fade_seconds("nan"), 0.0)
        self.assertEqual(pedal_fade_seconds("junk"), DEFAULT_PEDAL_FADE_S)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pedal_fade_seconds(), 3.0)
            self.assertEqual(release_fade_seconds(), 0.0)
            self.assertEqual(release_curve(), RELEASE_CURVES["linear"])
            self.assertEqual(PedalFade.from_env().release_fade_s, 0.0)
        self.assertEqual(release_fade_seconds("0"), 0.0)
        self.assertEqual(release_curve("Exponential"), RELEASE_CURVES["exponential"])
        with mock.patch.dict(os.environ, {"MPE_PEDAL_FADE_S": "0"}, clear=True):
            self.assertIsNone(PedalFade.from_env())


# Closed form with the defaults, 8 * (2/8) ** ((n - 21) / 87), worked by hand
# so a wrong formula in the module cannot also be the expected value.
CURVE = {21: 8.0, 36: 6.2992, 48: 5.2029, 60: 4.2974, 72: 3.5494, 84: 2.9317,
         108: 2.0}


class RegisterCurveTests(unittest.TestCase):
    """Hold time by pitch: long in the bass, short in the treble."""

    def test_curve_values(self) -> None:
        for note, hold in CURVE.items():
            self.assertAlmostEqual(register_hold_seconds(note, 8.0, 2.0), hold,
                                   places=3, msg=f"note {note}")

    def test_middle_c(self) -> None:
        # Geometric, not linear: linear would give 5.31 s here.
        self.assertAlmostEqual(register_hold_seconds(60, 8.0, 2.0), 4.2974, places=3)

    def test_equal_ratio_per_octave(self) -> None:
        ratios = {round(register_hold_seconds(n + 12, 8.0, 2.0)
                        / register_hold_seconds(n, 8.0, 2.0), 9)
                  for n in (24, 36, 48, 60, 72, 84)}
        self.assertEqual(len(ratios), 1)

    def test_clamped_outside_the_piano(self) -> None:
        for note in (0, 1, 20):
            self.assertEqual(register_hold_seconds(note, 8.0, 2.0), 8.0)
        for note in (109, 120, 127):
            self.assertEqual(register_hold_seconds(note, 8.0, 2.0), 2.0)

    def test_precedence(self) -> None:
        self.assertEqual(hold_register("", "", ""), (8.0, 2.0),
                         "nothing set: the curve, at its defaults")
        self.assertIsNone(hold_register("3", "", ""),
                          "only MPE_PEDAL_FADE_S set: the flat hold, as before")
        self.assertEqual(hold_register("3", "10", ""), (10.0, 2.0))
        self.assertEqual(hold_register("3", "", "1.5"), (8.0, 1.5))
        self.assertEqual(hold_register("", "0", "junk"), (8.0, 2.0),
                         "a bad end takes its default")

    def test_from_env(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            p = PedalFade.from_env()
            self.assertEqual(p.register, (8.0, 2.0))
            self.assertAlmostEqual(p.hold_s(60), 4.2974, places=3)
        with mock.patch.dict(os.environ, {"MPE_PEDAL_FADE_S": "3"}, clear=True):
            p = PedalFade.from_env()
            self.assertIsNone(p.register)
            self.assertEqual({p.hold_s(n) for n in (0, 21, 60, 108, 127)}, {3.0})
        with mock.patch.dict(os.environ, {"MPE_PEDAL_FADE_S": "0",
                                          "MPE_PEDAL_FADE_LOW_S": "8",
                                          "MPE_PEDAL_FADE_HIGH_S": "2"}, clear=True):
            self.assertIsNone(PedalFade.from_env(), "0 is off whatever else is set")


class RegisterHoldTests(unittest.TestCase):
    """The curve on the fake clock: the deadline comes from the note-off's note."""

    def setUp(self) -> None:
        self.p = PedalFade(FADE, register=(8.0, 2.0))
        self.p.filter(_pedal(0, True), 0.0)

    def _release(self, ch: int, note: int, at: float) -> None:
        self.p.filter(_on(ch, note), 0.0)
        self.assertEqual(self.p.filter(_off(ch, note), at), [])

    def test_bass_rings_longer_than_treble(self) -> None:
        self._release(1, 36, 1.0)
        self._release(2, 84, 1.0)
        self.assertEqual(self.p.due(1.0 + CURVE[84] - 0.001), [])
        self.assertEqual(self.p.due(1.0 + CURVE[84] + 0.001), [_off(2, 84)],
                         "treble first")
        self.assertEqual(self.p.due(1.0 + CURVE[36] - 0.001), [])
        self.assertEqual(self.p.due(1.0 + CURVE[36] + 0.001), [_off(1, 36)])
        self.assertEqual(self.p.due(100.0), [])

    def test_middle_c_deadline(self) -> None:
        self._release(1, 60, 1.0)
        self.assertAlmostEqual(self.p.next_deadline(), 1.0 + 4.2974, places=3)

    def test_clamped_deadlines(self) -> None:
        self._release(1, 5, 0.0)
        self._release(2, 120, 0.0)
        self.assertAlmostEqual(self.p.next_deadline(), 2.0)
        self.assertEqual(self.p.due(2.0), [_off(2, 120)])
        self.assertAlmostEqual(self.p.next_deadline(), 8.0)
        self.assertEqual(self.p.due(8.0), [_off(1, 5)])

    def test_pedal_up_still_releases_everything_at_once(self) -> None:
        self._release(1, 36, 1.0)
        self._release(2, 84, 1.0)
        sent = self.p.filter(_pedal(0, False), 1.5)
        self.assertEqual(_note_offs(sent), Counter({(1, 36): 1, (2, 84): 1}))
        self.assertEqual(self.p.due(100.0), [])


class ReleaseScheduleTests(unittest.TestCase):
    """The pedal-up curve, read as the share of notes still held per step."""

    def test_lone_note_lets_go_where_the_old_ramp_crossed_64(self) -> None:
        # Step 2 of [127, 85, 58, ...] is the first value below 64.
        self.assertEqual(release_schedule(1, 10.0, 2.0, RELEASE_CURVES["linear"]),
                         [10.4])

    def test_three_notes_spread_across_the_window(self) -> None:
        times = release_schedule(3, 0.0, 2.0, RELEASE_CURVES["linear"])
        self.assertEqual([round(t, 6) for t in times], [0.2, 0.4, 1.0])

    def test_every_note_is_scheduled_inside_the_window_in_order(self) -> None:
        for curve in RELEASE_CURVES.values():
            for n in range(1, 30):
                times = release_schedule(n, 5.0, 2.0, curve)
                self.assertEqual(len(times), n)
                self.assertEqual(times, sorted(times))
                self.assertGreater(times[0], 5.0, "never all at the pedal-up instant")
                self.assertLessEqual(times[-1], 7.0 + 1e-9)

    def test_zero_window_is_all_at_once(self) -> None:
        self.assertEqual(release_schedule(3, 4.0, 0.0, RELEASE_CURVES["linear"]),
                         [4.0, 4.0, 4.0])


class PedalUpWindowTests(unittest.TestCase):
    """MPE_PEDAL_RELEASE_FADE_S > 0: pedal-up note-offs spread, oldest first."""

    def setUp(self) -> None:
        self.p = PedalFade(10.0, 2.0, RELEASE_CURVES["linear"])
        self.p.filter(_pedal(0, True), 0.0)
        # Note-on order 64, 60, 67; key-release order 60, 67, 64.
        self.p.filter(_on(2, 64), 0.0)
        self.p.filter(_on(1, 60), 0.1)
        self.p.filter(_on(3, 67), 0.2)
        self.p.filter(_off(1, 60), 0.5)
        self.p.filter(_off(3, 67), 0.6)
        self.p.filter(_off(2, 64), 0.7)

    def test_pedal_up_spreads_note_offs_oldest_first(self) -> None:
        self.assertEqual(self.p.filter(_pedal(0, False), 1.0), [],
                         "nothing at the pedal-up instant")
        self.assertEqual(self.p.due(1.19), [])
        self.assertEqual(self.p.due(1.2), [_off(2, 64)], "oldest note-on first")
        self.assertEqual(self.p.due(1.39), [])
        self.assertEqual(self.p.due(1.4), [_off(1, 60)])
        self.assertEqual(self.p.due(1.99), [])
        self.assertEqual(self.p.due(2.0), [_off(3, 67)])
        self.assertEqual(self.p.due(100.0), [])

    def test_pedal_press_in_the_window_takes_the_rest_back(self) -> None:
        self.p.filter(_pedal(0, False), 1.0)
        self.assertEqual(self.p.due(1.2), [_off(2, 64)])
        self.assertEqual(self.p.filter(_pedal(0, True), 1.3), [])
        self.assertEqual(self.p.due(9.0), [], "the window's releases are cancelled")
        self.assertEqual(self.p.due(10.5), [_off(1, 60)],
                         "back to its in-hold deadline: release 0.5 + 10")
        self.assertEqual(self.p.due(10.6), [_off(3, 67)])
        self.assertEqual(self.p.due(100.0), [])

    def test_hold_deadline_still_applies_inside_the_window(self) -> None:
        p = PedalFade(1.0, 2.0, RELEASE_CURVES["linear"])
        p.filter(_pedal(0, True), 0.0)
        p.filter(_on(1, 60), 0.0)
        p.filter(_off(1, 60), 0.0)  # deadline 1.0
        p.filter(_pedal(0, False), 0.9)  # window would release it at 1.3
        self.assertEqual(p.due(1.0), [_off(1, 60)])
        self.assertEqual(p.due(100.0), [])

    def test_restrike_and_release_all_inside_the_window_are_one_note_off(self) -> None:
        self.p.filter(_pedal(0, False), 1.0)
        self.assertEqual(self.p.filter(_on(4, 60), 1.1), [_off(1, 60), _on(4, 60)])
        self.assertCountEqual(self.p.release_all(), [_off(2, 64), _off(3, 67)])
        self.assertEqual(self.p.due(100.0), [])

    def test_key_released_inside_the_window_passes(self) -> None:
        self.p.filter(_on(5, 72), 0.8)
        self.p.filter(_pedal(0, False), 1.0)
        self.assertEqual(self.p.filter(_off(5, 72), 1.1), [_off(5, 72)],
                         "the pedal is up: a release is a release")


class PedalUpAllAtOnceTests(unittest.TestCase):
    def test_default_pedal_up_releases_every_pedal_held_note_at_once(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            p = PedalFade.from_env()
        p.filter(_pedal(0, True), 0.0)
        p.filter(_on(2, 64), 0.0)
        p.filter(_on(1, 60), 0.1)
        p.filter(_on(4, 72), 0.2)
        p.filter(_off(2, 64), 0.5)
        p.filter(_off(1, 60), 0.6)
        sent = p.filter(_pedal(0, False), 1.0)
        self.assertEqual(_note_offs(sent), Counter({(2, 64): 1, (1, 60): 1}))
        self.assertEqual(p.due(100.0), [], "nothing left to release later")


class PedalFadeTests(unittest.TestCase):
    """The pure state machine, message by message."""

    def setUp(self) -> None:
        self.p = PedalFade(FADE)

    def test_release_under_pedal_sends_note_off_at_deadline_and_not_before(self) -> None:
        self.assertEqual(self.p.filter(_on(1, 60), 0.0), [_on(1, 60)])
        self.assertEqual(self.p.filter(_pedal(0, True), 0.5), [])
        self.assertEqual(self.p.filter(_off(1, 60, 33), 1.0), [],
                         "a key released under the pedal keeps sounding")
        self.assertEqual(self.p.next_deadline(), 1.0 + FADE)
        self.assertEqual(self.p.due(1.0 + FADE - 0.001), [], "not before T")
        self.assertEqual(self.p.due(1.0 + FADE), [_off(1, 60, 33)],
                         "at T, the key's own note-off, same channel and note")
        self.assertEqual(self.p.due(10.0), [], "and only once")
        self.assertIsNone(self.p.next_deadline())

    def test_each_note_gets_its_own_deadline(self) -> None:
        self.p.filter(_pedal(0, True), 0.0)
        for i, note in enumerate((60, 64, 67)):
            self.p.filter(_on(1 + i, note), 0.0)
            self.p.filter(_off(1 + i, note), 1.0 + i)
        self.assertEqual(self.p.due(1.0 + FADE), [_off(1, 60)])
        self.assertEqual(self.p.due(2.0 + FADE), [_off(2, 64)])
        self.assertEqual(self.p.due(3.0 + FADE), [_off(3, 67)])

    def test_pedal_up_releases_every_pedal_held_note_at_once(self) -> None:
        self.p.filter(_pedal(0, True), 0.0)
        for i, note in enumerate((60, 64, 67)):
            self.p.filter(_on(1 + i, note), 0.0)
            self.p.filter(_off(1 + i, note), 0.5)
        out = self.p.filter(_pedal(0, False), 1.0)
        self.assertCountEqual(out, [_off(1, 60), _off(2, 64), _off(3, 67)])
        self.assertEqual(self.p.due(100.0), [])

    def test_restrike_sends_old_note_off_before_new_note_on(self) -> None:
        self.p.filter(_pedal(0, True), 0.0)
        self.p.filter(_on(1, 60), 0.0)
        self.p.filter(_off(1, 60), 0.5)
        # MPE puts the re-strike on another member channel; same pitch.
        out = self.p.filter(_on(2, 60), 1.0)
        self.assertEqual(out, [_off(1, 60), _on(2, 60)])
        self.assertEqual(self.p.due(100.0), [], "the old instance is gone")
        # Same channel too.
        self.p.filter(_off(2, 60), 2.0)
        self.assertEqual(self.p.filter(_on(2, 60), 2.5), [_off(2, 60), _on(2, 60)])

    def test_held_keys_are_untouched(self) -> None:
        self.p.filter(_on(1, 60), 0.0)
        self.p.filter(_pedal(0, True), 0.1)
        self.p.filter(_on(2, 64), 0.2)
        self.p.filter(_off(2, 64), 0.3)
        self.assertEqual(self.p.due(0.3 + FADE), [_off(2, 64)])
        self.assertEqual(self.p.filter(_pedal(0, False), 50.0), [],
                         "pedal-up does not release a key that is down")
        self.assertEqual(self.p.due(100.0), [])
        self.assertEqual(self.p.filter(_off(1, 60), 100.0), [_off(1, 60)],
                         "a held key's note-off passes when the pedal is up")

    def test_release_without_pedal_passes(self) -> None:
        self.p.filter(_on(1, 60), 0.0)
        self.assertEqual(self.p.filter(_off(1, 60), 1.0), [_off(1, 60)])

    def test_cc64_is_never_forwarded(self) -> None:
        for ch in (0, 3):
            self.assertEqual(self.p.filter(_pedal(ch, True), 0.0), [])
            self.assertEqual(self.p.filter(_pedal(ch, False), 0.0), [])
        self.assertEqual(self.p.filter([0xB0, 1, 9], 0.0), [[0xB0, 1, 9]])

    def test_member_channel_pedal_holds_only_its_channel(self) -> None:
        self.p.filter(_pedal(2, True), 0.0)
        self.p.filter(_on(1, 60), 0.0)
        self.p.filter(_on(2, 62), 0.0)
        self.assertEqual(self.p.filter(_off(1, 60), 1.0), [_off(1, 60)])
        self.assertEqual(self.p.filter(_off(2, 62), 1.0), [])
        self.assertEqual(self.p.filter(_pedal(2, False), 2.0), [_off(2, 62)])

    def test_unknown_note_off_passes(self) -> None:
        self.p.filter(_pedal(0, True), 0.0)
        self.assertEqual(self.p.filter(_off(4, 70), 1.0), [_off(4, 70)])

    def test_panic_releases_pedal_held_explicitly(self) -> None:
        self.p.filter(_pedal(0, True), 0.0)
        self.p.filter(_on(1, 60), 0.0)
        self.p.filter(_off(1, 60), 0.5)
        self.p.filter(_on(2, 64), 0.6)
        out = self.p.filter([0xB0, 123, 0], 1.0)
        self.assertEqual(out, [_off(1, 60), [0xB0, 123, 0]])
        self.assertEqual(self.p.due(100.0), [])

    def test_forget_after_shed(self) -> None:
        self.p.filter(_pedal(0, True), 0.0)
        self.p.filter(_on(1, 60), 0.0)
        self.p.filter(_off(1, 60), 0.5)
        self.p.forget(1, 60)
        self.assertEqual(self.p.due(100.0), [])
        self.p.filter(_on(2, 64), 0.0)
        self.p.forget(2, 64)  # shed while the key is down
        self.assertEqual(self.p.filter(_off(2, 64), 1.0), [],
                         "the key's own note-off would be the second")
        self.assertEqual(self.p.due(100.0), [])

    def test_release_all(self) -> None:
        self.p.filter(_pedal(0, True), 0.0)
        self.p.filter(_on(1, 60), 0.0)
        self.p.filter(_off(1, 60), 0.5)
        self.p.filter(_on(2, 64), 0.0)
        self.assertEqual(self.p.release_all(), [_off(1, 60)])
        self.assertEqual(self.p.due(100.0), [])
        self.assertEqual(self.p.filter(_off(2, 64), 1.0), [_off(2, 64)],
                         "the pedal is up after release_all")


def _load_remap_module():
    spec = importlib.util.spec_from_file_location(
        "mpe_pressure_remap_pedal", REPO / "scripts" / "mpe-pressure-remap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RemapperPedalTests(unittest.TestCase):
    """The daemon's real send path, shed path and teardown, on a fake clock."""

    fade_env = "3"
    release_env = "0"

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
            mock.patch.dict(os.environ, {"MPE_PEDAL_FADE_S": self.fade_env,
                                         "MPE_PEDAL_RELEASE_FADE_S": self.release_env},
                            clear=True),
        ]
        for patch in self.patches:
            patch.start()
        self.daemon = self.module.PressureRemapDaemon()
        self.t = 0.0
        self.daemon._clock = lambda: self.t
        self.sent: list[list[int]] = []
        self.daemon._out = mock.Mock(send_message=lambda msg: self.sent.append(list(msg)))

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self._tmp.cleanup()

    def _at(self, t: float, msg: list[int]) -> None:
        self.t = t
        with mock.patch("patch_browser.poly_voice_tracker.time.monotonic", return_value=t):
            self.daemon._send(msg)

    def _tick(self, t: float) -> None:
        self.t = t
        self.daemon._process_pedal_deadlines(t)

    def _shed(self, limit: int, at: float) -> None:
        self.request.write_text(json.dumps(
            {"request_id": at, "requested_at": at, "limit": limit, "reason": "high"}))
        self.daemon._process_shed_request(at)

    def _pedal_chord(self) -> None:
        self._at(0.0, _pedal(0, True))
        for i, note in enumerate((60, 64, 67)):
            self._at(0.0, _on(1 + i, note))
            self._at(1.0, _off(1 + i, note))

    def test_cc64_is_not_forwarded_and_notes_end_at_deadline(self) -> None:
        self._pedal_chord()
        self.assertFalse(any(m[0] & 0xF0 == 0xB0 and m[1] == 64 for m in self.sent),
                         "Surge must never see CC 64 with the fade on")
        self.assertEqual(_note_offs(self.sent), Counter())
        self.assertEqual(self.daemon._voice_tracker.active_count(), 3)
        self._tick(1.0 + FADE - 0.01)
        self.assertEqual(_note_offs(self.sent), Counter())
        self._tick(1.0 + FADE)
        self.assertEqual(_note_offs(self.sent), Counter({(1, 60): 1, (2, 64): 1, (3, 67): 1}))
        self.assertEqual(self.daemon._voice_tracker.active_count(), 0,
                         "the governor's sounding count follows")

    def test_shed_then_deadline_is_one_note_off(self) -> None:
        self._pedal_chord()
        self._shed(limit=1, at=2.0)
        self.assertEqual(self.daemon._voice_tracker.active_count(), 1,
                         "pedal-held notes are sheddable now: a note-off frees them")
        self._tick(10.0)
        self._at(11.0, _pedal(0, False))
        self.assertEqual(set(_note_offs(self.sent).values()), {1})
        self.assertEqual(len(_note_offs(self.sent)), 3)
        self.assertEqual(self.daemon._voice_tracker.active_count(), 0)

    def test_shed_of_held_key_then_release_under_pedal_is_one_note_off(self) -> None:
        self._at(0.0, _on(1, 60))
        self._at(0.0, _on(2, 64))
        self._at(0.5, _pedal(0, True))
        self._shed(limit=1, at=1.0)
        self._at(1.5, _off(1, 60))
        self._at(1.5, _off(2, 64))
        self._tick(100.0)
        self._at(101.0, _pedal(0, False))
        self.assertEqual(_note_offs(self.sent), Counter({(1, 60): 1, (2, 64): 1}))
        self.assertEqual(self.daemon._voice_tracker.active_count(), 0)

    def test_pedal_up_at_the_deadline_is_one_note_off(self) -> None:
        self._pedal_chord()
        self._tick(1.0 + FADE)
        self._at(1.0 + FADE, _pedal(0, False))
        self.assertEqual(set(_note_offs(self.sent).values()), {1})

    def test_shutdown_releases_pedal_held_once(self) -> None:
        self._pedal_chord()
        self._at(1.5, _on(4, 70))  # a key still down at exit
        self.daemon._close_inputs()
        self.assertEqual(_note_offs(self.sent), Counter({(1, 60): 1, (2, 64): 1, (3, 67): 1}))
        self._tick(100.0)
        self.assertEqual(len(_note_offs(self.sent)), 3, "no deadline after shutdown")
        self.assertEqual(self.daemon._voice_tracker.active_count(), 1)

    def test_panic_releases_pedal_held_once(self) -> None:
        self._pedal_chord()
        self._at(2.0, [0xB0, 123, 0])
        self.assertEqual(self.sent[-1], [0xB0, 123, 0])
        self._tick(100.0)
        self.assertEqual(set(_note_offs(self.sent).values()), {1})
        self.assertEqual(self.daemon._voice_tracker.active_count(), 0)

    def test_restrike_ordering_on_the_wire(self) -> None:
        self._at(0.0, _pedal(0, True))
        self._at(0.0, _on(1, 60))
        self._at(0.5, _off(1, 60))
        self.sent.clear()
        self._at(1.0, _on(2, 60))
        self.assertEqual(self.sent, [_off(1, 60), _on(2, 60)])
        self._tick(100.0)
        self.assertEqual(self.sent, [_off(1, 60), _on(2, 60)])

    def test_deadlines_run_from_the_drain_loop(self) -> None:
        self._pedal_chord()
        with mock.patch.object(self.module.time, "monotonic", return_value=1.0 + FADE):
            self.daemon._drain_queue()
        self.assertEqual(len(_note_offs(self.sent)), 3)


class RemapperPedalUpWindowTests(RemapperPedalTests):
    """Every daemon race above again, with the 2 s pedal-up window on."""

    release_env = "2"

    def test_pedal_up_window_through_the_daemon(self) -> None:
        self._pedal_chord()
        self._at(2.0, _pedal(0, False))
        self.assertEqual(_note_offs(self.sent), Counter())
        self._tick(2.2)
        self.assertEqual(_note_offs(self.sent), Counter({(1, 60): 1}))
        self.assertEqual(self.daemon._voice_tracker.active_count(), 2)
        self._at(2.3, _pedal(0, True))
        self._shed(limit=1, at=2.35)  # sheds 64, the oldest left
        self._tick(1.0 + FADE)
        self._at(10.0, _pedal(0, False))
        self._tick(20.0)
        self.assertEqual(_note_offs(self.sent), Counter({(1, 60): 1, (2, 64): 1, (3, 67): 1}))
        self.assertEqual(self.daemon._voice_tracker.active_count(), 0)

    def test_pedal_up_at_the_deadline_is_one_note_off(self) -> None:
        self._pedal_chord()
        self._at(1.0 + FADE - 0.1, _pedal(0, False))
        self._tick(1.0 + FADE)
        self._tick(100.0)
        self.assertEqual(set(_note_offs(self.sent).values()), {1})
        self.assertEqual(len(_note_offs(self.sent)), 3)


class RemapperRegisterTests(unittest.TestCase):
    """Nothing set in the env: the daemon holds by pitch, bass longest."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.module = _load_remap_module()
        self.patches = [
            mock.patch("patch_browser.poly_voice_tracker.VOICE_TRACKER_FILE",
                       tmp / "poly-voice-tracker.json"),
            mock.patch("builtins.print"),
            mock.patch.dict(os.environ, {}, clear=True),
        ]
        for patch in self.patches:
            patch.start()
        self.daemon = self.module.PressureRemapDaemon()
        self.daemon._clock = lambda: 0.0  # every key released at t=0
        self.sent: list[list[int]] = []
        self.daemon._out = mock.Mock(send_message=lambda msg: self.sent.append(list(msg)))

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self._tmp.cleanup()

    def test_bass_note_off_reaches_surge_after_treble(self) -> None:
        for msg in (_pedal(0, True), _on(1, 36), _on(2, 84), _off(1, 36), _off(2, 84)):
            self.daemon._send(msg)
        self.daemon._process_pedal_deadlines(CURVE[84] + 0.01)
        self.assertEqual(_note_offs(self.sent), Counter({(2, 84): 1}))
        self.daemon._process_pedal_deadlines(CURVE[36] + 0.01)
        self.assertEqual(_note_offs(self.sent), Counter({(2, 84): 1, (1, 36): 1}))


class RemapperPedalOffTests(unittest.TestCase):
    """MPE_PEDAL_FADE_S=0: today's behaviour, CC 64 passed through untouched."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.module = _load_remap_module()
        self.patches = [
            mock.patch("patch_browser.poly_voice_tracker.VOICE_TRACKER_FILE",
                       tmp / "poly-voice-tracker.json"),
            mock.patch("builtins.print"),
            mock.patch.dict(os.environ, {"MPE_PEDAL_FADE_S": "0"}, clear=True),
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

    def test_stream_is_unchanged(self) -> None:
        self.assertIsNone(self.daemon._pedal)
        stream = [_pedal(0, True), _on(1, 60), _off(1, 60, 33), _pedal(0, False),
                  [0xB0, 64, 90], _on(2, 60)]
        for msg in stream:
            self.daemon._send(msg)
        self.daemon._process_pedal_deadlines(1e9)
        self.daemon._close_inputs()
        self.assertEqual(self.sent, stream, "byte-identical, CC 64 included")


if __name__ == "__main__":
    unittest.main()
