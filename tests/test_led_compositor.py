"""One writer to the wire — the invariant, and the defect it closes.

Charter §3, question 3: *"How would you know if that were violated?"* For every
LED on this surface the honest answer was **you would see it on the device**.
There was no test anywhere that asserted the device's resulting state rather
than one writer's outgoing messages, which is exactly why a suite of 1600
passed through four separate wrong-light defects without moving.

So the two tests that matter here are:

  * `OneWriterToTheWireTests` — no module but the compositor may send an LED
    byte, checked by reading the source. On the tree before this stage it names
    ten sites in five files.
  * `ReconnectTests` — finding H, end to end: `SlotSurface` and
    `TransportButtonLeds` on ONE wire, the exact `reopen_apc` sequence, then
    fifty poll cycles, asserting what the DEVICE shows. No test in the suite
    had ever constructed both of those objects at once, so the conflict between
    them was untestable by construction.

The rest pin the properties the compositor is supposed to have: that
submissions commute, that a transient hands its control back, that a capability
violation raises at the byte, and that a blink costs one message per blink
rather than one per poll.
"""

import ast
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

import control_registry as reg  # noqa: E402
from apc_grid import GRID_ROWS, GridView, pad_note  # noqa: E402
from apc_transport import (  # noqa: E402
    NOTE_SHIFT_MK2,
    NOTE_STOP_ALL_CLIPS_MK2,
    SCENE_LAUNCH_NOTES_MK2,
    TransportButtonLeds,
)
from led_compositor import (  # noqa: E402
    LAYER_BASE,
    LAYER_GESTURE,
    LAYER_HOLD,
    LAYER_SURFACE,
    LAYER_TRANSPORT,
    LAYERS,
    NOTE_ON_CH0,
    LedCompositor,
    UnknownControl,
)
from led_table import (  # noqa: E402
    LED_GREEN,
    LED_OFF,
    LED_RED,
    LED_YELLOW,
    SCENE_LED_BLINK,
    SCENE_LED_OFF,
    SCENE_LED_ON,
)
from sl_loop_states import SL_STATE_PLAYING  # noqa: E402
from slot_matrix import Slot, Track  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from slot_surface import SlotSurface  # noqa: E402
from track_gesture import TrackGesture  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: Every module that speaks to the APC. Mirrors `test_control_registry`.
APC_SOURCES = sorted(
    set((REPO / "scripts" / "sooperlooper").glob("*.py"))
    | set((REPO / "scripts").glob("*apc*.py"))
)


class Wire:
    """The device, as far as this process can see it."""

    def __init__(self) -> None:
        self.sent: list[list[int]] = []

    def send_message(self, msg) -> None:
        self.sent.append(list(msg))

    def state(self) -> dict[int, int]:
        """What each note is showing: the LAST value it was told."""
        showing: dict[int, int] = {}
        for status, note, velocity in self.sent:
            showing[note] = velocity
        return showing

    def since(self, mark: int) -> list[list[int]]:
        return self.sent[mark:]


# --- the invariant ----------------------------------------------------------


class OneWriterToTheWireTests(unittest.TestCase):
    """No button or pad LED byte leaves this process except through here.

    Spec §5.5: *"no button LED write that does not go through the
    compositor"*. `apc_panel`'s rule 2 and `apc_link`'s "eleven send_message
    sites" docstring were both true, correct, and unenforced — and both drifted.
    A rule a build cannot fail is not a rule.

    What the instrument sees, stated plainly: it reads the AST of the APC
    modules for a `send_message` call whose first argument is a list built in
    place, which is the shape all ten historical sites took. A message assembled
    into a variable first would get through. It catches every form that has
    actually existed here.
    """

    #: The one writer, and one deliberate exception.
    ALLOWED = {
        # The compositor. This is the seam.
        "led_compositor.py",
        # A separate process, run with the session stopped, whose entire job is
        # to paint raw messages nobody else would send and ask Mitch what lit
        # up. Pacing or diffing it would put the instrument between the probe
        # and the thing being probed. See its module docstring.
        "probe-apc-buttons.py",
        # Same category, same reason: a separate process, session stopped,
        # bisecting the note space to find Shift's lamp. It must paint notes
        # the compositor has no control for, and routing it through the
        # compositor would mean the instrument decides what the probe is
        # allowed to ask. See its module docstring.
        "probe-apc-shift-led.py",
        # Not an LED write and not to the APC: it opens a virtual port and
        # sends note-ons INTO the bench's input, standing in for a finger.
        # Same three bytes, opposite direction, different device. It lives in
        # this directory because it drives the bench, not because it paints it.
        "synthpad.py",
    }

    def _led_writes(self, path: Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "send_message"):
                continue
            if not node.args or not isinstance(node.args[0], ast.List):
                continue     # OSC takes a path and a list of arguments
            out.append(f"{path.name}:{node.lineno}")
        return out

    def test_no_led_byte_is_sent_outside_the_compositor(self) -> None:
        offenders = [
            site
            for path in APC_SOURCES
            if path.name not in self.ALLOWED
            for site in self._led_writes(path)
        ]
        self.assertEqual(
            offenders, [],
            "these send LED bytes without going through led_compositor, so "
            "nothing decides which of them the device ends up showing:\n"
            + "\n".join(offenders),
        )

    def test_the_compositor_still_writes(self) -> None:
        """Proof the check above is not vacuous — the seam has to be somewhere."""
        sites = self._led_writes(REPO / "scripts" / "sooperlooper" / "led_compositor.py")
        self.assertEqual(len(sites), 1, f"expected exactly one write site: {sites}")

    def test_no_private_diff_cache_survives(self) -> None:
        """The four caches, by name, so they cannot come back one at a time.

        `SlotSurface._painted`, `SlotSurface._scene_painted`,
        `TransportButtonLeds._last_vel` and `TrackGesture._led_last` were four
        records of one fact — what the device is showing — held by objects that
        did not own the wire and had never been told the others existed. The
        general mechanism: writer A sends note N and records it, writer B sends
        note N and records it, neither reads the other, and both then suppress
        the correction that would fix the panel.
        """
        gone = ("_painted", "_scene_painted", "_last_vel", "_led_last")
        offenders = []
        for path in APC_SOURCES:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in gone
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                ):
                    offenders.append(f"{path.name}:{node.lineno} self.{node.attr}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_paint_method_takes_a_force_flag(self) -> None:
        """`force=` was always "my record of the device is wrong".

        Which was never the caller's question to answer. The proof that these
        were papering over ownership rather than solving anything: at bench
        startup `repaint_scenes(force=True)` invalidated one cache to paint the
        truth, and sixty lines later a different writer's constructor undid it.
        A force flag defeated by another writer's force flag.

        The legitimate need — "the device came back dark, forget what we think
        it shows" — is one compositor operation, `invalidate()`, called by
        whoever learned the device came back dark.
        """
        offenders = []
        for path in APC_SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = [a.arg for a in node.args.args + node.args.kwonlyargs]
                if "force" in names:
                    offenders.append(f"{path.name}:{node.lineno} {node.name}()")
        self.assertEqual(offenders, [], "\n".join(offenders))


# --- finding H --------------------------------------------------------------


def _surface(wire: Wire, tmp: Path):
    """The compositor and the surface, as the bench builds them at line 367.

    Three tracks holding takes in slots 0-2, one of them playing: the state a
    player is in after ten minutes. Takes on disk, one running, everything else
    waiting to be launched.
    """
    leds = LedCompositor(wire, apc_label="mk2")
    rt = SlotRuntime(send=lambda p, a: None, clips_dir=tmp, num_tracks=15,
                     log=lambda m: None)
    by_loop = {}
    for loop in range(15):
        fs = TrackGesture(loop=loop, hold_ms=2000, debounce_ms=0,
                          multigrid=True, quantized=True)
        fs.bind(None, None, None)
        by_loop[loop] = fs
    surface = SlotSurface(
        runtime=rt,
        gestures_by_loop=by_loop,
        view=GridView(offset=0),
        compositor=leds,
        num_tracks=15,
        scene_launch_notes=SCENE_LAUNCH_NOTES_MK2,
        log=lambda m: None,
    )
    return leds, rt, by_loop, surface


def _transport(leds):
    """The transport lamps, as the bench builds them at line 438.

    Sixty lines after the surface has painted the scene column, which is the
    detail that mattered: `TransportButtonLeds.__init__` called
    `clear_unwired_surfaces()`, and the surface's forced paint was already in
    its private cache, so the column stayed dark for the session.

    No test in the suite had ever built both objects at once — the only file
    that mentions `TransportButtonLeds` contains zero references to
    `SlotSurface` — so the conflict between them could not be expressed.
    """
    return TransportButtonLeds(
        compositor=leds,
        shift_note=NOTE_SHIFT_MK2,
        stop_all_note=NOTE_STOP_ALL_CLIPS_MK2,
        hold_s=3.0,
        apc_label="mk2",
    )


class ReconnectTests(unittest.TestCase):
    """Finding H: the reconnect erased the matrix, and the erasure stuck.

    `reopen_apc` painted the matrix, painted the scene column, then called
    `transport_leds.repaint()` -> `clear_unwired_surfaces()`, which darkened
    `RESERVED_GRID_NOTES` (8-63, rows 1-7 — the entire matrix) and all eight
    scene notes. Twelve lit controls went dark. The forced repaint had already
    written the full 64-entry map into `SlotSurface._painted`, so the next
    diffing repaint saw no change and sent nothing: measured at fifty poll
    cycles, zero messages. The panel stayed wrong until a cell's desired
    colour happened to change — for an idle set, never.

    The one recovery path in the code was a bank change, and banking is dead on
    the attached mk2 (the arrow notes are the scene column). Restarting the
    session was the only fix, and nothing on the surface said so.

    Live since `MPE_SL_MULTIGRID=1`, which `/etc/mpe/mpe.env` sets and the
    running process's environ confirms. Re-enumeration is not hypothetical:
    `apc_link`'s own docstring records four session starts in six leaving the
    pads dead, and `LinkHealth` re-checks and reopens on a 2 s timer.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wire = Wire()
        self.leds, self.rt, self.by_loop, self.surface = _surface(self.wire, self.tmp)
        for track in range(3):
            self.rt._tracks[track] = Track(
                slots=(Slot("a.wav"), Slot("b.wav"), Slot("c.wav"), *([None] * 5)),
                active_slot=0,
            )
        self.by_loop[0].sl_state = SL_STATE_PLAYING
        self.surface.on_state(0, SL_STATE_PLAYING)
        self.surface.repaint()
        self.surface.repaint_scenes()
        # Built last, exactly as the bench does. Under the compositor this
        # ordering stops mattering — that is the claim the stage is making.
        self.transport = _transport(self.leds)

    def _expected(self) -> dict[int, int]:
        """What the panel should show: green playing, yellow stored, dark empty."""
        want = {}
        for col in range(8):
            for row in range(8):
                note = pad_note(row, col)
                if col > 2 or row > 2:
                    want[note] = LED_OFF
                elif col == 0 and row == 0:
                    want[note] = LED_GREEN
                elif row == 0:
                    want[note] = LED_OFF    # active slot, track not playing
                else:
                    want[note] = LED_YELLOW
        return want

    def _reopen(self) -> None:
        """Exactly the bench's `reopen_apc`, minus the rtmidi calls."""
        self.surface.repaint()
        self.surface.repaint_scenes()
        self.leds.invalidate()

    def test_the_matrix_still_shows_the_takes_after_a_reconnect(self) -> None:
        self._reopen()
        showing = self.wire.state()
        for note, colour in self._expected().items():
            self.assertEqual(
                showing.get(note), colour,
                f"pad {note:#04x} shows {showing.get(note)}, want {colour} — "
                "the player's stored takes are gone from the grid",
            )

    def test_the_takes_stay_shown_across_fifty_poll_cycles(self) -> None:
        """The half that made it permanent rather than transient.

        A wrong light that repairs itself on the next poll is a glitch. A wrong
        light whose repair is suppressed by a stale cache is a dead panel, and
        the only difference between the two is which object holds the record of
        what the device shows.
        """
        self._reopen()
        mark = len(self.wire.sent)
        for _ in range(50):
            self.surface.poll_hold()
            self.surface.poll_hold_led()
            self.surface.poll_led_repaint()
            self.transport.poll()
        self.assertEqual(
            self.wire.since(mark), [],
            "a steady surface must be silent — anything here is two writers "
            "arguing at poll rate",
        )
        showing = self.wire.state()
        for note, colour in self._expected().items():
            self.assertEqual(showing.get(note), colour, f"pad {note:#04x}")

    def test_the_scene_column_is_lit_from_session_start(self) -> None:
        """The same collision, at startup, on the scene column alone.

        `TransportButtonLeds.__init__` ran AFTER the bench painted the scene
        column, and darkened all eight notes on construction. So under
        multigrid the scene launch buttons have been dark since session start —
        invisible, because a dark scene button is also what a correct idle one
        looks like.
        """
        showing = self.wire.state()
        # Rows 0-2 hold clips on three tracks, none of them fully playing.
        for row in range(3):
            note = SCENE_LAUNCH_NOTES_MK2[GRID_ROWS - 1 - row]
            self.assertEqual(
                showing.get(note), SCENE_LED_ON,
                f"scene button for row {row} is dark over a row that holds clips",
            )
        for row in range(3, 8):
            note = SCENE_LAUNCH_NOTES_MK2[GRID_ROWS - 1 - row]
            self.assertEqual(showing.get(note), SCENE_LED_OFF)

    def test_one_tap_of_stop_all_does_not_kill_scene_row_zero(self) -> None:
        """F3, which fired every single time rather than on a race.

        Stop All is 0x77 on mk2, and 0x77 is grid row 0's scene launcher —
        "Stop All Clips" is a SHIFT layer on the same physical button.
        `SlotSurface` painted it as a scene indicator while
        `TransportButtonLeds._apply` drove it as the held lamp, submitting
        SCENE_LED_OFF whenever nothing was held. One tap and row 0's indicator
        was dark for the rest of the session while the surface believed it lit.
        The button that means "this scene holds clips" became identical to the
        one that means "empty, does nothing" — a wrong light a player acts on.
        """
        row_zero = SCENE_LAUNCH_NOTES_MK2[-1]
        self.assertEqual(self.wire.state()[row_zero], SCENE_LED_ON)
        self.transport.note_event(NOTE_STOP_ALL_CLIPS_MK2, True)
        self.assertEqual(self.wire.state()[row_zero], SCENE_LED_ON, "held = lit")
        self.transport.note_event(NOTE_STOP_ALL_CLIPS_MK2, False)
        self.assertEqual(
            self.wire.state()[row_zero], SCENE_LED_ON,
            "row 0 still holds clips, so its indicator must come back",
        )


# --- the properties the compositor is supposed to have -----------------------


class PriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wire = Wire()
        self.leds = LedCompositor(self.wire, apc_label="mk2")
        self.note = SCENE_LAUNCH_NOTES_MK2[-1]

    def test_submissions_commute(self) -> None:
        """The point of the stage, stated as an equation.

        Today's resolution is not call order — it is which writer's private
        cache happens to disagree with its own last desired value, which is
        worse, because call order would at least be deterministic. Under a
        declared priority the same two submissions produce the same panel
        whichever way round they arrive, so an ordering like `reopen_apc`'s
        cannot be written wrong.
        """
        for order in ((LAYER_SURFACE, LAYER_TRANSPORT), (LAYER_TRANSPORT, LAYER_SURFACE)):
            with self.subTest(order=order):
                wire = Wire()
                leds = LedCompositor(wire, apc_label="mk2")
                values = {LAYER_SURFACE: SCENE_LED_BLINK, LAYER_TRANSPORT: SCENE_LED_ON}
                for layer in order:
                    leds.submit(layer, {self.note: values[layer]})
                self.assertEqual(wire.state()[self.note], SCENE_LED_ON)

    def test_a_transient_hands_the_control_back(self) -> None:
        self.leds.submit(LAYER_SURFACE, {self.note: SCENE_LED_BLINK})
        self.leds.submit(LAYER_TRANSPORT, {self.note: SCENE_LED_ON})
        self.assertEqual(self.wire.state()[self.note], SCENE_LED_ON)
        self.leds.submit(LAYER_TRANSPORT, {self.note: None})
        self.assertEqual(self.wire.state()[self.note], SCENE_LED_BLINK)

    def test_the_base_layer_cannot_erase_an_owner(self) -> None:
        """`clear_unwired_surfaces`, done the way round that works.

        Its job — clear a lamp left lit by a previous build — is real. What was
        wrong was that it ran as a *writer*, last, so it erased owners that had
        already spoken. As the lowest-priority layer it does the same job and
        cannot.
        """
        self.leds.submit(LAYER_SURFACE, {pad_note(3, 3): LED_GREEN})
        self.leds.submit(LAYER_BASE, {pad_note(3, 3): LED_OFF})
        self.assertEqual(self.wire.state()[pad_note(3, 3)], LED_GREEN)

    def test_every_layer_declares_a_priority_and_a_reason(self) -> None:
        self.assertEqual(len({l.priority for l in LAYERS}), len(LAYERS))
        for layer in LAYERS:
            with self.subTest(layer.name):
                self.assertTrue(layer.what.strip(),
                                "a layer with no stated job is an ownership hole")

    def test_a_steady_surface_is_silent(self) -> None:
        self.leds.submit(LAYER_SURFACE, {pad_note(0, 0): LED_GREEN})
        mark = len(self.wire.sent)
        for _ in range(50):
            self.leds.submit(LAYER_SURFACE, {pad_note(0, 0): LED_GREEN})
        self.assertEqual(self.wire.since(mark), [])

    def test_invalidate_re_asserts_everything(self) -> None:
        self.leds.submit(LAYER_SURFACE, {pad_note(0, 0): LED_GREEN})
        mark = len(self.wire.sent)
        self.leds.invalidate()
        resent = {m[1]: m[2] for m in self.wire.since(mark)}
        self.assertEqual(resent[pad_note(0, 0)], LED_GREEN)
        self.assertEqual(len(resent), len(reg.lit_notes("mk2")))

    def test_the_status_byte_is_channel_zero_and_nothing_else(self) -> None:
        """`device_facts.apc.buttons.channel_response`, MEASURED 2026-08-29.

        Sixteen channels painted at once; only 0x90 lit. The channel axis is
        exhausted, not sampled. The grid's brightness/blink channels are added
        downstream by `apc_leds.translate`, which is the one place that knows
        which model is attached.
        """
        self.leds.submit(LAYER_SURFACE, {pad_note(0, 0): LED_GREEN})
        self.assertTrue(self.wire.sent)
        self.assertTrue(all(m[0] == NOTE_ON_CH0 for m in self.wire.sent))


class CapabilityTests(unittest.TestCase):
    """Spec §5.4, given its first production callers.

    `Fact.refuse_with()` was described in the spec as making rule 4
    "executable rather than aspirational". Until 2026-08-30 it had never
    executed: `fact()`, `refuse_with()`, `unmeasured()` and `AUTHORITATIVE` had
    zero callers anywhere in the repo, and five modules cited a fact id that has
    never existed. The compositor is where the rule now runs, on the value about
    to become bytes.
    """

    def setUp(self) -> None:
        self.wire = Wire()
        self.leds = LedCompositor(self.wire, apc_label="mk2")

    def test_a_yellow_scene_button_raises(self) -> None:
        """The exact request the spec names, and it is now impossible to ship.

        `apc.scene.led_observed` and `apc.buttons.single_colour` are MEASURED,
        five probe rounds on 2026-08-29 with a positive control on the grid
        pads: green only, three states, channel axis exhausted, SysEx RGB
        rejected. A promise of yellow scene buttons once passed 1575 tests and
        shipped, because a velocity is just an int.

        It is the vocabulary table rather than `check_colour` that refuses,
        and that is the stronger refusal: on a button there is no velocity that
        MEANS yellow, so the request is not "a colour this lamp cannot show",
        it is a sentence the control cannot parse.
        """
        with self.assertRaises(reg.CapabilityViolation) as caught:
            self.leds.submit(LAYER_SURFACE, {SCENE_LAUNCH_NOTES_MK2[0]: LED_YELLOW})
        self.assertIn("not in the vocabulary", str(caught.exception))

    def test_a_red_scene_button_raises(self) -> None:
        """Red is velocity 3 on the grid and nothing on a button.

        Worth its own test because of the near miss beside it: the grid's
        LED_GREEN and LED_GREEN_BLINK are 1 and 2, which are also the button's
        on and blink. So the scene column has been painted with grid constants
        the whole time and looked right, and only red and yellow would ever
        have exposed it.
        """
        with self.assertRaises(reg.CapabilityViolation):
            self.leds.submit(LAYER_SURFACE, {SCENE_LAUNCH_NOTES_MK2[0]: LED_RED})

    def test_an_unaddressable_lamp_warns_and_does_not_refuse(self) -> None:
        """`check_colour` running in production, on rule 4's own split.

        Shift has an LED. We do not know how to reach it — `apc.shift.led` has
        no established addressing — and "we cannot reach it" is a different
        sentence from "it cannot do that". Refusing here would be this code
        telling Mitch his hardware cannot do something on the strength of not
        having tried, which is the failure `device_facts` exists to prevent: it
        happened twice on 2026-08-29 and cost two wrong builds.

        So it warns, passes the byte, and the panel keeps working.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.leds.submit(LAYER_SURFACE, {NOTE_SHIFT_MK2: 1})
        self.assertEqual([w.category for w in caught], [reg.CapabilityUnmeasured])
        self.assertEqual(self.wire.state()[NOTE_SHIFT_MK2], 1)

    def test_the_grid_takes_every_colour_led_table_has(self) -> None:
        for colour in (LED_OFF, LED_GREEN, LED_RED, LED_YELLOW):
            with self.subTest(colour=colour):
                self.leds.submit(LAYER_SURFACE, {pad_note(2, 2): colour})
        self.assertEqual(self.wire.state()[pad_note(2, 2)], LED_YELLOW)

    def test_a_button_takes_off_on_and_blink_and_nothing_else(self) -> None:
        note = SCENE_LAUNCH_NOTES_MK2[0]
        for velocity in (SCENE_LED_OFF, SCENE_LED_ON, SCENE_LED_BLINK):
            with self.subTest(velocity=velocity):
                self.leds.submit(LAYER_SURFACE, {note: velocity})
        with self.assertRaises(reg.CapabilityViolation):
            self.leds.submit(LAYER_SURFACE, {note: 42})

    def test_an_unregistered_note_warns_rather_than_blacking_out_the_panel(self) -> None:
        """A note the registry cannot name is a gap in the registry.

        Refusing to paint it would let that gap black out the panel, which is
        the failure mode this branch exists to end. It is passed through and
        said out loud, once, so the fix is to add the row.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.leds.submit(LAYER_SURFACE, {0x7F: 1})
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, UnknownControl)
        self.assertEqual(self.wire.state()[0x7F], 1)


class HoldBlinkTests(unittest.TestCase):
    """F5: the one writer with no record of what it had sent.

    `poll_hold_led` wrote straight to the wire on every call. Measured in a
    free-running loop: **87,174 messages to a single note in 0.30 s**, against
    `PacedMidiOut`'s budget of one per 1.5 ms (~666/s). At the bench's real
    cadence that is ~400 messages a second, so a 1.5 s hold spent most of the
    LED bandwidth re-asserting two velocities and every other repaint queued
    behind it on an unbounded deque.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wire = Wire()
        self.leds, self.rt, self.by_loop, self.surface = _surface(self.wire, self.tmp)
        self.clock = [1000.0]
        self.surface._now = lambda: self.clock[0]

    def test_a_hold_costs_one_message_per_blink_not_one_per_poll(self) -> None:
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), Slot("b.wav"), *([None] * 6)),
                                   active_slot=0)
        self.surface.repaint()
        note = pad_note(1, 0)          # an occupied, non-active slot: hold = delete
        self.surface.note_down(note)
        mark = len(self.wire.sent)
        # 1.5 s of holding, polled at the bench's ~485 Hz.
        for _ in range(728):
            self.clock[0] += 1 / 485
            self.surface.poll_hold_led()
        blinks = self.wire.since(mark)
        self.assertTrue(blinks, "the hold warning must be visible")
        self.assertLessEqual(
            len(blinks), 8,
            f"{len(blinks)} messages for 1.5 s of a 2 Hz blink — the write rate "
            "is following the poll rate again",
        )
        self.assertTrue(all(m[1] == note for m in blinks))

    def test_the_warning_leaves_the_pad_when_the_finger_does(self) -> None:
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), Slot("b.wav"), *([None] * 6)),
                                   active_slot=0)
        self.surface.repaint()
        note = pad_note(1, 0)
        self.surface.note_down(note)
        self.clock[0] += 0.6
        self.surface.poll_hold_led()
        self.surface.note_up(note)
        self.assertEqual(
            self.wire.state()[note], LED_YELLOW,
            "the pad holds a stored take, so it goes back to yellow",
        )


class BlinkMeaningTests(unittest.TestCase):
    """F18: `blink` means three different things on one column of eight buttons.

    This mattered less while it was one visual token among seven. It matters
    now: `apc.buttons.single_colour` is MEASURED and CLOSED as a bounded
    negative, so a scene button has exactly three states and blink is one third
    of the whole vocabulary available on it.

    Which meaning should win is a UI judgement — charter §6, Mitch's eye, and
    not something a test can decide. What the architecture owes is that the
    conflict is countable rather than something a player discovers by not being
    able to read their panel, and that trying an answer costs one line.
    """

    def test_the_scene_column_carries_two_meanings_of_blink(self) -> None:
        wire = Wire()
        leds = LedCompositor(wire, apc_label="mk2")
        note = SCENE_LAUNCH_NOTES_MK2[-1]      # Stop All / scene row 0
        leds.submit(LAYER_SURFACE, {note: SCENE_LED_BLINK})
        leds.submit(LAYER_TRANSPORT, {note: SCENE_LED_ON})
        conflicts = leds.blink_conflicts()
        self.assertIn(note, conflicts, "the known conflict must be reported")
        meanings = dict(conflicts[note])
        self.assertEqual(sorted(meanings), [LAYER_SURFACE, LAYER_TRANSPORT])
        self.assertNotEqual(meanings[LAYER_SURFACE], meanings[LAYER_TRANSPORT])

    def test_no_third_meaning_has_appeared(self) -> None:
        """Three was already too many. A fourth arrives silently otherwise."""
        meanings = {l.blink for l in LAYERS if l.blink}
        self.assertEqual(len(meanings), 4, sorted(meanings))

    def test_firmware_blink_and_software_blink_are_handed_over_explicitly(self) -> None:
        """They do not compose: a firmware blink is not stopped by writing
        `on`, it is replaced, and whichever writer stops last leaves the lamp
        wherever it last wrote. The compositor makes every transition an
        explicit write by construction — there is no path where one animator
        simply stops and another takes over mid-phase.
        """
        wire = Wire()
        leds = LedCompositor(wire, apc_label="mk2")
        note = SCENE_LAUNCH_NOTES_MK2[-1]
        leds.submit(LAYER_SURFACE, {note: SCENE_LED_BLINK})
        mark = len(wire.sent)
        leds.submit(LAYER_TRANSPORT, {note: SCENE_LED_ON})
        self.assertEqual(wire.since(mark), [[NOTE_ON_CH0, note, SCENE_LED_ON]])
        mark = len(wire.sent)
        leds.submit(LAYER_TRANSPORT, {note: None})
        self.assertEqual(wire.since(mark), [[NOTE_ON_CH0, note, SCENE_LED_BLINK]])


class LayerOwnershipTests(unittest.TestCase):
    def test_every_layer_names_a_module_the_registry_knows(self) -> None:
        """A layer owned by a module nobody declared is an ownership hole with
        a nice name on it."""
        for layer in LAYERS:
            if not layer.owner:
                continue
            with self.subTest(layer.name):
                self.assertIn(layer.owner, reg.OWNERS)

    def test_the_gesture_and_the_surface_never_both_paint_the_clip_row(self) -> None:
        """Mode selects the owner, never both.

        `TrackGesture._set_led`'s multigrid early-return is the one place
        ownership was genuinely enforced before this branch, and it is the
        template: the gesture computes colour and writes nothing, the surface
        reads `current_led()` and paints.
        """
        wire = Wire()
        leds = LedCompositor(wire, apc_label="mk2")
        fs = TrackGesture(loop=0, hold_ms=2000, debounce_ms=0, multigrid=True)
        fs.bind(None, leds, pad_note(0, 0))
        fs._set_led(LED_GREEN)
        self.assertEqual(wire.sent, [], "the gesture painted under multigrid")
        self.assertNotIn(LAYER_GESTURE, leds.contention().get(pad_note(0, 0), ()))

    def test_the_hold_layer_outranks_the_matrix(self) -> None:
        """The delete warning is the only feedback for a destructive action.

        It used to be pre-empted non-deterministically: `poll_holds()` ran
        `poll_hold_led()` and then `poll_led_repaint()`, so within one
        iteration the repaint won whenever it had anything to send — which
        depended on unrelated engine traffic.
        """
        wire = Wire()
        leds = LedCompositor(wire, apc_label="mk2")
        note = pad_note(1, 0)
        leds.submit(LAYER_HOLD, {note: LED_RED})
        leds.submit(LAYER_SURFACE, {note: LED_YELLOW})
        self.assertEqual(wire.state()[note], LED_RED)


if __name__ == "__main__":
    unittest.main()
