"""APC transport combo (Shift + Stop All Clips hold)."""

from tests import conftest  # noqa: F401 — bare sooperlooper imports (led_table, …)

import unittest
from unittest.mock import patch

from scripts.sooperlooper.apc_transport import (
    MK1_GHOST_SHIFT_S,
    MK1_TRACK_OVERLAP_NOTES,
    NOTE_SHIFT_MK1,
    NOTE_SHIFT_MK2,
    NOTE_STOP_ALL_CLIPS_MK1,
    NOTE_STOP_ALL_CLIPS_MK2,
    NOTE_TRACK8_MK2,
    SCENE_LAUNCH_NOTES_MK1,
    Mk1ShiftGhostFilter,
    ShiftHoldCombo,
    TransportButtonLeds,
    resolve_apc_transport_notes,
    resolve_scene_launch_notes,
    scene_row_for_note,
)
from scripts.sooperlooper.control_registry import lit_notes
from scripts.sooperlooper.led_compositor import LedCompositor
from scripts.sooperlooper.led_table import (
    LED_OFF,
    SCENE_LED_OFF,
    SCENE_LED_ON,
    TRACK_LED_ON,
    accelerating_hold_blink_on,
)


class ResolveApcTransportNotesTests(unittest.TestCase):
    def test_mk1_from_port_name(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC MINI:APC MINI MIDI 1 32:0")
        self.assertEqual(label, "mk1")
        self.assertEqual(shift, NOTE_SHIFT_MK1)
        self.assertEqual(stop, NOTE_STOP_ALL_CLIPS_MK1)

    def test_mk2_from_port_name(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC mini mk2")
        self.assertEqual(label, "mk2")
        self.assertEqual(shift, NOTE_SHIFT_MK2)
        self.assertEqual(stop, NOTE_STOP_ALL_CLIPS_MK2)

    def test_explicit_variant_override(self) -> None:
        shift, stop, label = resolve_apc_transport_notes("APC MINI", variant="mk2")
        self.assertEqual(label, "mk2")
        self.assertEqual(shift, NOTE_SHIFT_MK2)

    def test_the_mk2_stale_lamp_is_cleared_at_startup_and_never_lit(self) -> None:
        """mk2 Track Select 8, dark, once — and nothing ever lights it.

        It has been lit for the wrong reason twice, in opposite directions: as
        a "Shift is held" lamp ("pressing shift lights up column 8"), then as
        the clear-all warning, which took the blink off the button under the
        player's finger. The note is kept only so a lamp left on by an earlier
        build gets cleared, and that clear is now the compositor's base layer
        rather than four methods of a live writer re-asserting it.

        This replaces two tests that asserted `resolve_stale_lamp_note`'s
        return value. That function had no production caller left once the
        clear moved, and a resolver nobody calls proves nothing about the
        panel — spec §5.3, "nothing outside the compositor may send a button
        LED byte."
        """
        sent: list[list[int]] = []

        class Out:
            def send_message(self, m) -> None:
                sent.append(list(m))

        leds = LedCompositor(Out(), apc_label="mk2")
        leds.invalidate()
        self.assertIn([0x90, NOTE_TRACK8_MK2, 0], sent)
        self.assertEqual(
            [m for m in sent if m[1] == NOTE_TRACK8_MK2 and m[2] != 0], []
        )

    def test_the_mk1_track_row_is_not_painted_at_all(self) -> None:
        """Its note numbers are recall, and they contradict each other.

        `apc_transport` claimed 0x37 for button 8 while `apc_panel` claims
        0x64-0x6B for the row; neither has evidence. Darkening a note we cannot
        name is how you turn off something you did not mean to, so the mk1
        track row stays out of `lit_notes` until someone presses the buttons
        with `--dump-midi` running. That is what `resolve_stale_lamp_note`
        used to say by hand as "mk1 has none".
        """
        self.assertNotIn(NOTE_TRACK8_MK2, lit_notes("mk1"))
        self.assertIn(NOTE_TRACK8_MK2, lit_notes("mk2"))

    def test_mk1_scene_launch_notes(self) -> None:
        self.assertEqual(resolve_scene_launch_notes("mk1"), SCENE_LAUNCH_NOTES_MK1)
        # 0x59 IS a scene launcher — row 0's. Its "Stop All Clips" label is a
        # Shift layer, and the bench has always required Shift+0x59 for that,
        # so excluding it from the column left the bottom row unreachable.
        self.assertIn(NOTE_STOP_ALL_CLIPS_MK1, resolve_scene_launch_notes("mk1"))
        self.assertEqual(len(resolve_scene_launch_notes("mk1")), 8)

    def test_scene_row_for_note(self) -> None:
        notes = resolve_scene_launch_notes("mk1")
        # The side buttons run top-to-bottom, the grid rows bottom-to-top, so
        # the mapping is a reflection and not an identity: the TOP button is
        # the TOP row. An identity here put every scene one end of the grid
        # away from the row the player pressed.
        # Eight buttons, eight rows, running opposite ways: the top button
        # (0x52) is beside the top row, the bottom one (0x59) beside row 0.
        # 0x59's "Stop All" is a Shift layer, so alone it is row 0's launcher.
        self.assertEqual(scene_row_for_note(notes, notes[0]), 7)
        self.assertEqual(scene_row_for_note(notes, notes[7]), 0)
        self.assertEqual(scene_row_for_note(notes, NOTE_STOP_ALL_CLIPS_MK1), 0)


class Mk1ShiftGhostFilterTests(unittest.TestCase):
    def test_ghost_stop_and_scene_after_shift(self) -> None:
        """The mechanism, exercised with an explicit window. It is OFF by
        default since SP8 — see test_mk1_ghost_filter_is_off_by_default."""
        filt = Mk1ShiftGhostFilter(
            shift_note=NOTE_SHIFT_MK1,
            stop_all_note=NOTE_STOP_ALL_CLIPS_MK1,
            scene_launch_notes=SCENE_LAUNCH_NOTES_MK1,
            ghost_s=0.08,
        )
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[0.0, 0.01, 0.01, 0.01],
        ):
            filt.note_event(NOTE_SHIFT_MK1, True, now=0.0)
            self.assertTrue(filt.consume(NOTE_STOP_ALL_CLIPS_MK1, True, now=0.01))
            self.assertTrue(filt.consume(SCENE_LAUNCH_NOTES_MK1[0], True, now=0.01))
            self.assertTrue(filt.consume(MK1_TRACK_OVERLAP_NOTES[6], True, now=0.01))

    def test_intentional_stop_after_ghost_window(self) -> None:
        filt = Mk1ShiftGhostFilter(
            shift_note=NOTE_SHIFT_MK1,
            stop_all_note=NOTE_STOP_ALL_CLIPS_MK1,
            scene_launch_notes=SCENE_LAUNCH_NOTES_MK1,
        )
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[0.0, 0.2],
        ):
            filt.note_event(NOTE_SHIFT_MK1, True, now=0.0)
            self.assertFalse(filt.consume(NOTE_STOP_ALL_CLIPS_MK1, True, now=0.2))


class ShiftHoldComboTests(unittest.TestCase):
    def test_fires_long_after_hold_with_both_down(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[10.0, 12.9, 13.0, 14.0],
        ):
            combo.note_event(122, True)
            combo.note_event(119, True)
            self.assertFalse(combo.poll_long())
            self.assertTrue(combo.poll_long())
            self.assertFalse(combo.poll_long())

    def test_short_on_release_before_hold(self) -> None:
        combo = ShiftHoldCombo(
            shift_note=NOTE_SHIFT_MK1,
            target_note=NOTE_STOP_ALL_CLIPS_MK1,
            hold_s=3.0,
        )
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[10.0, 10.2, 10.3],
        ):
            combo.note_event(NOTE_SHIFT_MK1, True)
            combo.note_event(NOTE_STOP_ALL_CLIPS_MK1, True)
            combo.note_event(NOTE_STOP_ALL_CLIPS_MK1, False)
            combo.note_event(NOTE_SHIFT_MK1, False)
            self.assertTrue(combo.poll_short())
            self.assertFalse(combo.poll_short())

    def test_releases_cancel_long_pending(self) -> None:
        combo = ShiftHoldCombo(shift_note=122, target_note=119, hold_s=3.0)
        combo.note_event(122, True)
        combo.note_event(119, True)
        self.assertTrue(combo.both_down)
        combo.note_event(122, False)
        self.assertFalse(combo.both_down)
        combo.note_event(119, False)
        self.assertFalse(combo.poll_long())


class ArrowBankingTests(unittest.TestCase):
    """Arrow note resolution.

    REWRITTEN 2026-08-30, stage 1. The previous version asserted that
    `resolve_arrow_notes` returns `ARROW_NOTES_MK2` and never compared that
    tuple against `SCENE_COLUMN_MK2` — which it sat inside. It was named for
    arrow banking and would have passed with arrow banking deleted, so it
    passed for weeks over a feature that has never worked on the attached
    hardware. Charter §2: "a test whose failure would not have caught the bug
    it is named for is a test that needs rewriting, even if it passes today."

    The mk2 tuple is now empty. That follows from canon, not from a guess:
    `device_facts.apc.buttons.note_sets` (MEASURED, rank 1) puts the eight
    scene buttons at 0x70-0x77, and the recalled arrow tuple (rank 6, VENDOR)
    claimed four of them. Rank 1 beats rank 6 and the lower tier is wrong until
    re-measured — so the mk2 arrow notes are recorded as unknown rather than
    replaced with another guess. See `device_facts.apc.bank_arrows.notes`.
    """

    def test_variant_resolution_matches_the_transport_notes_path(self) -> None:
        from scripts.sooperlooper.apc_transport import (
            ARROW_NOTES_MK1,
            resolve_arrow_notes,
        )

        self.assertEqual(sorted(resolve_arrow_notes("APC MINI")), sorted(ARROW_NOTES_MK1))
        self.assertEqual(resolve_arrow_notes("APC MINI")[ARROW_NOTES_MK1[0]], "up")
        # Explicit variant beats the port name, same as Shift/Stop-All.
        self.assertEqual(
            sorted(resolve_arrow_notes("APC mini mk2", variant="mk1")),
            sorted(ARROW_NOTES_MK1),
        )

    def test_no_arrow_note_is_also_a_scene_button(self) -> None:
        """The assertion whose absence let banking die.

        Not "the resolver returns the tuple we wrote down" — that is true of
        any tuple. This asks whether the tuple can survive contact with the
        rest of the panel, which is the only question that mattered.
        """
        from scripts.sooperlooper.apc_panel import SCENE_COLUMN_MK1, SCENE_COLUMN_MK2
        from scripts.sooperlooper.apc_transport import resolve_arrow_notes

        for port, scene_notes in (
            ("APC MINI", SCENE_COLUMN_MK1),
            ("APC mini mk2 MIDI 1", SCENE_COLUMN_MK2),
        ):
            with self.subTest(port=port):
                clash = set(resolve_arrow_notes(port)) & set(scene_notes)
                self.assertEqual(
                    clash,
                    set(),
                    f"{port}: {[hex(n) for n in sorted(clash)]} is claimed by "
                    "both the bank arrows and the scene column. The scene "
                    "branch of the bench event loop runs first and continues, "
                    "so these presses never reach handle_arrow.",
                )

    def test_mk2_arrow_notes_are_unknown_not_guessed(self) -> None:
        """Empty, and empty on the record.

        A future session that wants banking back on the mk2 needs `--dump-midi`
        and four presses, not a tuple. This pins the "unknown" so it cannot be
        quietly refilled with the same recall.
        """
        from scripts.sooperlooper.apc_transport import resolve_arrow_notes
        from scripts.sooperlooper.control_registry import DISPUTED
        from scripts.sooperlooper.device_facts import VENDOR, fact

        self.assertEqual(resolve_arrow_notes("APC mini mk2 MIDI 1"), {})
        refuted = {
            note
            for d in DISPUTED
            if d.variant == "mk2" and d.control_id.startswith("bank_")
            for note in d.claimed
        }
        self.assertEqual(sorted(refuted), [0x70, 0x71, 0x72, 0x73])
        # VENDOR on purpose: rule 4 forbids using it to say banking is
        # impossible. It records that we do not know the notes.
        self.assertEqual(fact("apc.bank_arrows.notes").tier, VENDOR)

    def test_up_down_page_by_eight_arrows_nudge_only_with_shift(self) -> None:
        from scripts.sooperlooper.apc_transport import bank_delta_for_arrow

        self.assertEqual(bank_delta_for_arrow("down", shift_down=False), 8)
        self.assertEqual(bank_delta_for_arrow("up", shift_down=False), -8)
        self.assertEqual(bank_delta_for_arrow("right", shift_down=False), 0)
        self.assertEqual(bank_delta_for_arrow("right", shift_down=True), 1)
        self.assertEqual(bank_delta_for_arrow("left", shift_down=True), -1)


class TransportButtonLedsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[list[int]] = []

        class FakeOut:
            def __init__(self, sink: list[list[int]]) -> None:
                self._sink = sink

            def send_message(self, message: list[int]) -> None:
                self._sink.append(list(message))

        self.midi_out = FakeOut(self.sent)
        self.shift = NOTE_SHIFT_MK1
        self.stop = NOTE_STOP_ALL_CLIPS_MK1
        self.leds = None

    def _leds(self, *, hold_s: float = 3.0, apc_label: str = "mk1") -> TransportButtonLeds:
        # The notes must match the variant. Building an mk2 object with mk1
        # notes made every mk2 note_event fall through the `else: return`, so
        # the assertion read a leftover message from construction.
        shift, stop = (
            (NOTE_SHIFT_MK2, NOTE_STOP_ALL_CLIPS_MK2) if apc_label == "mk2"
            else (self.shift, self.stop)
        )
        self.leds = LedCompositor(self.midi_out, apc_label=apc_label)
        return TransportButtonLeds(
            compositor=self.leds,
            shift_note=shift,
            stop_all_note=stop,
            hold_s=hold_s,
            apc_label=apc_label,
        )

    def test_mk1_shift_alone_says_nothing_about_the_scene_row(self) -> None:
        """Shift alone must not touch the scene column at all.

        It used to darken all eight scene buttons and grid notes 8-63, via
        `clear_unwired_surfaces`, on every Shift-down and on every poll while
        Shift was held. Under `MPE_SL_MULTIGRID=1` those 64 controls belong to
        `SlotSurface`, and its private diff cache then suppressed the repair —
        so reaching for Shift wiped the matrix until a colour happened to
        change. The mk1 ghost it was compensating for was refuted twice on
        hardware (SP6 and SP8, 2026-08-27: Shift alone emits 0x62 and nothing
        else) and `MK1_GHOST_SHIFT_S` is 0.

        Canon: `apc-control-surface-architecture-spec` §5.3 — "nothing outside
        the compositor may send a button LED byte", and the compositor
        resolves by declared priority rather than by who wrote last.
        """
        leds = self._leds()
        leds.note_event(self.shift, True)
        scene = [m for m in self.sent if m[1] in SCENE_LAUNCH_NOTES_MK1]
        self.assertEqual(scene, [], "Shift is a modifier, not a paint command")
        leds.note_event(self.shift, False)

    def test_mk1_stop_all_right_after_shift_is_a_real_chord(self) -> None:
        """The regression SP8 unblocks: a fast Shift+Stop All must register.

        10 ms after Shift is inside the old 80 ms window, so this press used
        to be discarded as a ghost. A human chording two buttons routinely
        lands there.
        """
        leds = self._leds()
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[0.0, 0.01, 0.01],
        ):
            leds.note_event(self.shift, True)
            leds.note_event(self.stop, True)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_ON])

    def test_mk1_stop_all_alone_lights_scene_green(self) -> None:
        leds = self._leds()
        leds.note_event(self.stop, True)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_ON])
        leds.note_event(self.stop, False)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_OFF])

    def test_releasing_stop_all_hands_the_button_back(self) -> None:
        """One tap of Stop All must not kill scene row 0's indicator.

        Stop All is grid row 0's scene launcher; "Stop All Clips" is a SHIFT
        layer on the same physical button. `SlotSurface` paints it to say
        whether row 0 holds clips. The transport used to submit SCENE_LED_OFF
        when nothing was held — an opinion, and the last one written — so a
        single tap of the most-used transport button on the panel left row 0
        dark for the rest of the session while the surface believed it was
        lit. The button that means "this scene holds clips" became identical
        to the one that means "empty, does nothing".

        Canon: `apc-control-surface-architecture-spec` §5.3 — owners submit
        desired state and the compositor resolves by priority. A transient
        releases; it does not paint over the owner underneath.
        """
        from scripts.sooperlooper.led_compositor import LAYER_SURFACE
        from scripts.sooperlooper.led_table import SCENE_LED_BLINK

        leds = self._leds()
        # The surface says row 0 is fully playing: blink, press to stop.
        self.leds.submit(LAYER_SURFACE, {self.stop: SCENE_LED_BLINK})
        leds.note_event(self.stop, True)
        self.assertEqual(self.leds.believes()[self.stop], SCENE_LED_ON)
        leds.note_event(self.stop, False)
        self.assertEqual(
            self.leds.believes()[self.stop], SCENE_LED_BLINK,
            "the scene indicator underneath must come back",
        )

    def test_mk1_both_held_blinks_stop_all_only(self) -> None:
        """A deliberate Shift+Stop All hold. No longer has to dodge a ghost
        window — SP8 refuted the ghost and MK1_GHOST_SHIFT_S is 0."""
        leds = self._leds(hold_s=3.0)
        with patch(
            "scripts.sooperlooper.apc_transport.time.monotonic",
            side_effect=[0.0, 0.01, 0.5, 0.5],
        ):
            leds.note_event(self.shift, True)
            leds.note_event(self.stop, True)
            self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_ON])
            leds.poll()
            self.assertIn(self.sent[-1][2], (SCENE_LED_ON, SCENE_LED_OFF))

    def test_mk2_shift_alone_lights_nothing(self) -> None:
        """Shift is a modifier, not a track state.

        Track Select 8 used to light whenever Shift was held. It is a control
        with its own meaning, so lighting it for an unrelated modifier says
        something false about track 8 — reported from the device as "pressing
        shift causes column 8 rec arm to light up".
        """
        leds = self._leds(apc_label="mk2")
        leds.note_event(NOTE_SHIFT_MK2, True)
        self.assertNotIn([0x90, NOTE_TRACK8_MK2, TRACK_LED_ON], self.sent)

    def test_the_clear_hold_blinks_stop_all_on_both_models(self) -> None:
        """The blink stays on the button being held.

        This shipped briefly with the blink moved onto Track Select 8, on the
        reasoning that Stop All is green-only so a red warning had to live
        somewhere else. It meant the button under the player's finger showed
        nothing at all while they held it. The hardware cannot do red here;
        that is a fact to state, not to route around.
        """
        for label, shift, stop in (
            ("mk1", NOTE_SHIFT_MK1, NOTE_STOP_ALL_CLIPS_MK1),
            ("mk2", NOTE_SHIFT_MK2, NOTE_STOP_ALL_CLIPS_MK2),
        ):
            with self.subTest(label):
                self.sent.clear()
                leds = self._leds(apc_label=label)
                leds.note_event(shift, True)
                leds.note_event(stop, True)
                blinks = [m for m in self.sent if m[1] == stop]
                self.assertEqual(blinks[-1][2], SCENE_LED_ON, "held = lit")

    def test_track_8_is_never_lit_by_the_bench(self) -> None:
        """Not for Shift, not for the clear hold, not for anything.

        Track Select 8 is a track control. Every time the bench has borrowed it
        to report something else, it has been read on the device as a fault in
        track 8 — twice now, in opposite directions.
        """
        leds = self._leds(apc_label="mk2")
        self.leds.invalidate()      # the startup clear, which IS allowed
        leds.note_event(NOTE_SHIFT_MK2, True)
        leds.note_event(NOTE_STOP_ALL_CLIPS_MK2, True)
        leds.poll()
        lit = [m for m in self.sent if m[1] == NOTE_TRACK8_MK2 and m[2] != 0]
        self.assertEqual(lit, [], f"track 8 was lit: {lit}")

    def test_reset_clears_until_both_released_mk1(self) -> None:
        leds = self._leds()
        leds.note_event(self.shift, True)
        leds.note_event(self.stop, True)
        leds.on_reset_fired()
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_OFF])
        leds.note_event(self.shift, True)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_OFF])
        leds.note_event(self.shift, False)
        leds.note_event(self.stop, False)
        leds.note_event(self.shift, True)
        self.assertEqual(self.sent[-1], [0x90, self.stop, SCENE_LED_OFF])

    def test_shift_never_touches_the_matrix(self) -> None:
        """The transport writes ONE note: Stop All, while it is held.

        This replaces `test_mk1_shift_clears_scene_and_upper_grid`, which
        asserted that holding Shift darkened every scene note and every grid
        note 8-63. That test wrote the defect down as a requirement: under
        multigrid those are the 64 controls `SlotSurface` owns, and the
        darkening is what erased the player's takes. It contradicts
        `apc-control-surface-architecture-spec` §5.3 and cannot survive the
        compositor, so it is replaced rather than adjusted.
        """
        for label in ("mk1", "mk2"):
            with self.subTest(label):
                self.sent.clear()
                leds = self._leds(apc_label=label)
                shift = NOTE_SHIFT_MK2 if label == "mk2" else NOTE_SHIFT_MK1
                leds.note_event(shift, True)
                leds.poll()
                leds.note_event(shift, False)
                self.assertEqual(
                    [m for m in self.sent if m[1] <= 0x63], [],
                    "the transport wrote a grid pad",
                )

    def test_mk1_ghost_filter_is_off_by_default(self) -> None:
        """SP8 refuted the ghost. A non-zero default would silently eat the
        Shift+Scene chord again."""
        self.assertEqual(MK1_GHOST_SHIFT_S, 0.0)

    def test_ghost_filter_still_works_when_a_window_is_configured(self) -> None:
        """The mechanism is kept for another mk1 unit that does ghost —
        MPE_APC_MK1_GHOST_S brings it back with no code change."""
        f = Mk1ShiftGhostFilter(
            shift_note=self.shift,
            stop_all_note=self.stop,
            scene_launch_notes=SCENE_LAUNCH_NOTES_MK1,
            ghost_s=0.08,
        )
        f.note_event(self.shift, True, now=0.0)
        self.assertTrue(f.consume(SCENE_LAUNCH_NOTES_MK1[0], True, now=0.01))
        self.assertFalse(f.consume(SCENE_LAUNCH_NOTES_MK1[0], True, now=0.5))

    def test_zero_window_passes_the_chord_through(self) -> None:
        f = Mk1ShiftGhostFilter(
            shift_note=self.shift,
            stop_all_note=self.stop,
            scene_launch_notes=SCENE_LAUNCH_NOTES_MK1,
            ghost_s=0.0,
        )
        f.note_event(self.shift, True, now=0.0)
        self.assertFalse(f.consume(SCENE_LAUNCH_NOTES_MK1[0], True, now=0.001))


class AcceleratingHoldBlinkTests(unittest.TestCase):
    def test_not_in_blink_window_before_delay(self) -> None:
        self.assertIsNone(
            accelerating_hold_blink_on(0.4, hold_s=2.0, blink_after_s=0.5)
        )

    def test_blinks_after_delay(self) -> None:
        first = accelerating_hold_blink_on(0.6, hold_s=2.0, blink_after_s=0.5)
        self.assertIn(first, (True, False))
