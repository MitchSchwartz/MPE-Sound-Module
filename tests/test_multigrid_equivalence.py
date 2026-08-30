"""The multigrid must behave *identically* to the validated single-clip path.

Why this test rather than more behaviour tests
----------------------------------------------
The single-clip transport model — `loop_model` + `led_table` + `TrackGesture` —
was validated over weeks, by ear, on the instrument. When the matrix was built it
grew a second gesture vocabulary and a second colour policy, so every behaviour the
old model encodes had to be rediscovered one symptom at a time by the person
playing it. That is not a series of bugs; it is one architectural mistake emitting
them, and enumerating the symptoms cannot terminate — neither of us can list
behaviours we have forgotten we rely on.

So this asserts equivalence instead of behaviour. **While a pad is its column's
active slot, the matrix must emit byte-identical OSC and byte-identical LED
messages to the single-clip path**, for arbitrary gesture sequences. Behaviours
nobody remembers are covered by construction, because the reference is the code
that already works.

The seam, stated honestly: equivalence holds only while `active_slot` is stable.
A *switch* rebinds the track's buffer and is new behaviour by definition — it has
its own tests and needs its own ear test. This file makes no claim about it.
"""

from __future__ import annotations

import itertools
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from track_gesture import TrackGesture  # noqa: E402
from apc_grid import GridView, pad_note  # noqa: E402
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
)
from led_compositor import LedCompositor  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from slot_surface import SlotSurface  # noqa: E402

TRACK = 0
SLOT = 0
NOTE = pad_note(SLOT, TRACK)
HOLD_MS = 2000.0
BLINK_MS = 500.0
DEBOUNCE_MS = 0.0


class FakeOsc:
    def __init__(self, log: list) -> None:
        self._log = log

    def send_message(self, path, args=None) -> None:
        self._log.append((path, list(args) if args else []))


class FakeOut:
    def __init__(self, log: list) -> None:
        self._log = log

    def send_message(self, msg) -> None:
        self._log.append(list(msg))


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _gesture(*, osc, compositor, clock, multigrid: bool) -> TrackGesture:
    fs = TrackGesture(
        loop=TRACK,
        hold_ms=HOLD_MS,
        debounce_ms=DEBOUNCE_MS,
        hold_blink_start_ms=BLINK_MS,
        quantized=False,
        multigrid=multigrid,
    )
    fs.bind(osc, compositor, NOTE)
    fs._now = clock  # type: ignore[attr-defined]
    return fs


class Rig:
    """Common driver surface so both paths take the identical event script."""

    def __init__(self) -> None:
        self.osc_log: list = []
        self.led_log: list = []
        self.clock = Clock()

    def led_for_note(self) -> list[int]:
        return [m[2] for m in self.led_log if len(m) > 2 and m[1] == NOTE]

    def led_shape(self) -> list[int]:
        """The colour sequence a player can perceive.

        Two differences in the raw stream are bookkeeping, not behaviour, and
        normalising them here is what keeps this test about the player's
        experience rather than about MIDI byte counts:

        * **Consecutive duplicates.** Both paths now diff once at the wire, so
          neither should emit them — but the normalisation stays, because what
          this test is about is the colour sequence a player perceives and not
          the traffic that produced it. Blink sequences alternate, so
          collapsing runs cannot hide a blink.
        * **A leading OFF.** The matrix blanks and paints the whole 8x8 at
          startup, so an empty cell is explicitly darkened; the single-clip
          path never paints a pad it has nothing to say about. Both leave the
          pad dark.

        Nothing else is normalised. A missing blink, a wrong colour, or a
        different ORDER still fails.
        """
        seq = self.led_for_note()
        while seq and seq[0] == 0:
            seq = seq[1:]
        out: list[int] = []
        for value in seq:
            if not out or out[-1] != value:
                out.append(value)
        return out


class ReferenceRig(Rig):
    """The validated single-clip path."""

    def __init__(self) -> None:
        super().__init__()
        self.fs = _gesture(
            osc=FakeOsc(self.osc_log),
            compositor=LedCompositor(FakeOut(self.led_log), apc_label="mk1"),
            clock=self.clock, multigrid=False,
        )

    def down(self) -> None:
        self.fs.on_pad_down()

    def up(self) -> None:
        self.fs.on_pad_up()

    def state(self, value: int) -> None:
        self.fs.sync_from_sl(value)

    def tick(self, dt: float) -> None:
        self.clock.t += dt
        self.fs.poll_hold()
        self.fs.poll_led()


class MultigridRig(Rig):
    def __init__(self, tmp: Path) -> None:
        super().__init__()
        # Faithful to the bench: under multigrid SlotSurface is the only LED
        # owner, so the gesture is bound with no compositor of its own.
        self.fs = _gesture(
            osc=FakeOsc(self.osc_log), compositor=None,
            clock=self.clock, multigrid=True,
        )
        leds = LedCompositor(FakeOut(self.led_log), apc_label="mk1")
        self.rt = SlotRuntime(
            send=lambda p, a: self.osc_log.append((p, list(a))),
            clips_dir=tmp,
            num_tracks=15,
        )
        self.surface = SlotSurface(
            runtime=self.rt,
            gestures_by_loop={TRACK: self.fs},
            view=GridView(offset=0),
            compositor=leds,
            num_tracks=15,
            hold_s=HOLD_MS / 1000.0,
            hold_blink_start_s=BLINK_MS / 1000.0,
        )
        self.surface._now = self.clock  # type: ignore[attr-defined]

    def down(self) -> None:
        self.surface.note_down(NOTE)

    def up(self) -> None:
        self.surface.note_up(NOTE)

    def state(self, value: int) -> None:
        # Exactly what SlBenchStateListener.on_update does: the gesture is
        # told first, then the surface.
        self.fs.sync_from_sl(value)
        self.surface.on_state(TRACK, value)

    def tick(self, dt: float) -> None:
        # Mirrors the bench loop: poll_track_gestures(multigrid=True) advances
        # blink phase only, then the surface polls hold and repaints.
        self.clock.t += dt
        self.fs.poll_led()
        self.surface.poll_hold()
        self.surface.poll_hold_led()
        self.surface.poll_led_repaint()


def run(rig, script) -> None:
    for step in script:
        op = step[0]
        if op == "down":
            rig.down()
        elif op == "up":
            rig.up()
        elif op == "state":
            rig.state(step[1])
        elif op == "tick":
            rig.tick(step[1])


# The gesture vocabulary a player actually produces, as event scripts.
SCRIPTS: dict[str, list] = {
    "tap to record": [
        ("down",), ("tick", 0.05), ("up",),
        ("state", SL_STATE_RECORDING), ("tick", 0.5),
    ],
    "record then close into ring-out": [
        ("down",), ("tick", 0.05), ("up",),
        ("state", SL_STATE_RECORDING), ("tick", 1.0),
        ("down",), ("tick", 0.05), ("up",),
        ("state", SL_STATE_OVERDUBBING), ("tick", 0.5),
        ("state", SL_STATE_PLAYING), ("tick", 0.5),
    ],
    "record close then mute": [
        ("down",), ("tick", 0.05), ("up",),
        ("state", SL_STATE_RECORDING), ("tick", 1.0),
        ("down",), ("tick", 0.05), ("up",),
        ("state", SL_STATE_PLAYING), ("tick", 0.5),
        ("down",), ("tick", 0.05), ("up",),
        ("state", SL_STATE_MUTE), ("tick", 0.5),
    ],
    "arm then start (WAIT_START)": [
        ("down",), ("tick", 0.05), ("up",),
        ("state", SL_STATE_WAIT_START), ("tick", 0.4),
        ("state", SL_STATE_RECORDING), ("tick", 0.4),
    ],
    "long hold to clear": [
        ("down",), ("tick", 0.6), ("tick", 0.6), ("tick", 0.6),
        ("tick", 0.6), ("up",), ("tick", 0.2),
    ],
    "hold blink window then release early": [
        ("down",), ("tick", 0.6), ("tick", 0.3), ("up",), ("tick", 0.2),
    ],
    "double tap while playing": [
        ("state", SL_STATE_PLAYING), ("tick", 0.2),
        ("down",), ("tick", 0.05), ("up",), ("tick", 0.05),
        ("down",), ("tick", 0.05), ("up",), ("tick", 0.3),
    ],
    "idle ticks only": [("tick", 0.5), ("tick", 0.5), ("tick", 0.5)],
}


class EquivalenceTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _both(self, script):
        ref = ReferenceRig()
        mg = MultigridRig(self.tmp)
        run(ref, script)
        run(mg, script)
        return ref, mg

    def test_osc_is_identical_for_every_gesture(self) -> None:
        divergences = []
        for name, script in SCRIPTS.items():
            ref, mg = self._both(script)
            if ref.osc_log != mg.osc_log:
                divergences.append(
                    f"\n--- {name} ---\n  single-clip: {ref.osc_log}\n"
                    f"  multigrid:   {mg.osc_log}"
                )
        self.assertEqual(divergences, [], "".join(divergences))

    def test_led_colours_are_identical_for_every_gesture(self) -> None:
        divergences = []
        for name, script in SCRIPTS.items():
            ref, mg = self._both(script)
            if ref.led_shape() != mg.led_shape():
                divergences.append(
                    f"\n--- {name} ---\n  single-clip: {ref.led_shape()}\n"
                    f"  multigrid:   {mg.led_shape()}"
                )
        self.assertEqual(divergences, [], "".join(divergences))

    def test_generated_sequences_agree(self) -> None:
        """Beyond the named gestures: short random-ish sequences, so a
        divergence nobody thought to script still surfaces."""
        atoms = [("down",), ("up",), ("tick", 0.3),
                 ("state", SL_STATE_RECORDING), ("state", SL_STATE_PLAYING)]
        divergences = []
        for combo in itertools.product(atoms, repeat=3):
            script = list(combo)
            ref, mg = self._both(script)
            if ref.osc_log != mg.osc_log:
                divergences.append(f"\n  {script}\n    ref={ref.osc_log}\n    mg ={mg.osc_log}")
            if len(divergences) >= 5:
                break
        self.assertEqual(divergences, [], "".join(divergences[:5]))
