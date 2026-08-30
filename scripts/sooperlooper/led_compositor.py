"""One writer to the wire, and one record of what the wire was told.

WHY THIS FILE EXISTS

Ten sites sent `[0x90, note, velocity]` to the APC. Four of them kept a private
dict of what they thought the device showed — `SlotSurface._painted`,
`SlotSurface._scene_painted`, `TransportButtonLeds._last_vel`,
`TrackGesture._led_last` — and one kept nothing at all. None of them knew the
others existed.

The spec's D2 says the winner "depends on call order in the event loop". That
is the optimistic reading. With four caches the winner depends on whose *cache*
happens to disagree with its own last desired value, which is not even
deterministic. What that cost, measured on this branch on 2026-08-30 by running
the real classes against a recording `midi_out`:

  * `reopen_apc` painted the matrix, painted the scene column, then called
    `transport_leds.repaint()` -> `clear_unwired_surfaces()`, which darkened
    grid notes 8-63 and all eight scene notes. Twelve lit controls went dark.
    Because the forced repaint had already written the full 64-entry map into
    `_painted`, the next fifty diffing repaints sent **zero** messages. After
    any USB re-enumeration — and `apc_link`'s own docstring records four starts
    in six leaving the pads dead — the player's stored takes vanished from the
    grid and never came back. The only recovery in the code was a bank change,
    and banking is dead on the attached mk2.
  * The same ordering collision fires at startup on the scene column alone
    (`TransportButtonLeds.__init__` runs after the bench's first paint), so
    under `MPE_SL_MULTIGRID=1` the scene launch buttons had been dark since
    session start. Invisible, because a dark scene button is exactly what a
    correct idle one looks like.
  * `poll_hold_led` had no cache at all: **87,174 messages to a single note in
    0.30 s** in a free-running loop, against a pacing budget of ~666/s.

Every `force=` flag in the old code was a manual cache invalidation issued
because the cache's owner did not own the wire. The proof is that they defeated
each other: the bench's startup `repaint_scenes(force=True)` was undone sixty
lines later by a different writer's constructor. With one cache there is one
invalidation — `invalidate()`, meaning "the device came back dark, forget what
we think it shows" — and no caller needs a flag.

HOW IT WORKS

Owners submit *desired state* for the controls they own. They never send bytes.
Each submission goes into a named LAYER; layers have a declared priority; the
winner for a note is the highest-priority layer holding an opinion about it.
`None` means "I have no opinion", which is how a transient hands a control back
rather than pinning it dark — the bug that made one tap of Stop All kill scene
row 0's indicator for the rest of the session.

Resolution is by priority, not by call order, so submissions commute: paint the
matrix then the transport, or the transport then the matrix, and the device ends
in the same state. That property is the whole stage. It is what makes the
`reopen_apc` ordering above impossible to write rather than merely fixed.

Writes are checked at this boundary — `control_registry.check_colour` — so a
velocity a control has been MEASURED not to show raises here, at the byte, and
`device_facts` rule 4 decides whether it raises or warns. Before 2026-08-30 the
fact base had no callers anywhere in the repo and the spec's claim that rule 4
was "executable rather than aspirational" was false.

WHAT IS NOT HERE

Colour policy. `led_table.led_for`, `slot_leds.static_cell_led` and
`slot_matrix.scene_row_led` decide what a control should show; this module
decides only that exactly one thing says it, once, and that the device is told
the truth about it. Per-model encoding also stays where it is — `PacedMidiOut`
runs `apc_leds.translate` on the way out — so what this module diffs is the
semantic velocity, which is the only vocabulary all the owners share.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Iterable, Mapping

import control_registry
from control_registry import BLINK, GRID, OFF, ON
from led_table import (
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
)

#: The one status byte. `device_facts.apc.buttons.channel_response` (MEASURED
#: 2026-08-29) closes the channel axis as EXHAUSTED, not sampled: all sixteen
#: channels were painted at once and only 0x90 lit. The grid's channel scheme
#: (0x96 solid, 0x9D blink) is applied downstream by `apc_leds.translate`, which
#: is the one place that knows which model is attached.
NOTE_ON_CH0 = 0x90

_ABSENT = object()


@dataclass(frozen=True)
class Layer:
    """One source of opinions about lamps, and what outranks what.

    `blink` is not decoration. `apc.buttons.single_colour` (MEASURED) closes a
    scene or track button at exactly three states — off, on, blink — so on those
    controls `blink` is one third of the entire vocabulary, and today it means
    two different things on one column of eight buttons. Naming the meaning per
    layer is what lets `blink_conflicts()` report it as data instead of leaving
    it as something a player discovers by not being able to read their panel.
    """

    name: str
    priority: int
    owner: str
    what: str
    blink: str


#: Lowest priority first. A layer outranks everything below it, for one stated
#: reason each — there is no "these cooperate" row, and adding one would be the
#: defect this module was built to end.
LAYERS: tuple[Layer, ...] = (
    Layer(
        name="base",
        priority=0,
        owner="",
        what="every lamp this session may write, dark. Seeded once at "
             "construction so a lamp left lit by a previous build, by Ableton, "
             "or by a crash is cleared — the job `clear_unwired_surfaces` was "
             "written for. It is the LOWEST priority, which is the whole "
             "difference: it can no longer erase an owner that has spoken.",
        blink="",
    ),
    Layer(
        name="gesture",
        priority=10,
        owner="track_gesture",
        what="the clip row, in single-clip mode. Mutually exclusive with "
             "`surface` by MPE_SL_MULTIGRID; ranked below it because when "
             "multigrid is on the matrix is already the declared authority and "
             "`TrackGesture._set_led` returns without writing.",
        blink="asked for and not yet confirmed by the engine",
    ),
    Layer(
        name="surface",
        priority=20,
        owner="slot_surface",
        what="the 8x8 matrix and the scene column, under multigrid.",
        blink="on a pad: queued, lands on the next bar. On a scene button: "
              "every clip in this row is already playing, so the press stops "
              "them — which is engine truth, the INVERSE of the pad rule.",
    ),
    Layer(
        name="transport",
        priority=30,
        owner="apc_transport",
        what="Stop All while it is held, and the accelerating Shift+Stop All "
             "clear warning. A transient: it releases the control the moment "
             "the finger lifts, and the scene indicator underneath comes back.",
        blink="you have been holding the clear combo for N seconds — a hold "
              "timer, neither engine truth nor queued intent",
    ),
    Layer(
        name="hold",
        priority=40,
        owner="slot_surface",
        what="the delete-hold warning on the pad under the finger. Top "
             "priority because it is the only feedback for a destructive "
             "action, and it used to be pre-empted non-deterministically by "
             "whatever the repaint happened to have to say that iteration.",
        blink="keep holding and this take is deleted",
    ),
)

LAYER_BASE = "base"
LAYER_GESTURE = "gesture"
LAYER_SURFACE = "surface"
LAYER_TRANSPORT = "transport"
LAYER_HOLD = "hold"

_BY_NAME: dict[str, Layer] = {layer.name: layer for layer in LAYERS}
#: Highest priority first — the order `_winner` walks.
_ORDER: tuple[str, ...] = tuple(
    layer.name for layer in sorted(LAYERS, key=lambda l: -l.priority)
)

if len({layer.priority for layer in LAYERS}) != len(LAYERS):
    raise ValueError(
        "two layers share a priority. Equal priority over overlapping controls "
        "is the ambiguity this module exists to remove — pick an order and "
        "write down why it is that way round."
    )


class UnknownControl(UserWarning):
    """A note was painted that no control in the registry claims."""


#: Grid velocity -> (colour, mode). `led_table`'s whole vocabulary.
_GRID_MEANING: dict[int, tuple[str | None, str]] = {
    LED_OFF: (None, OFF),
    LED_GREEN: (control_registry.GREEN, ON),
    LED_GREEN_BLINK: (control_registry.GREEN, BLINK),
    LED_RED: (control_registry.RED, ON),
    LED_RED_BLINK: (control_registry.RED, BLINK),
    LED_YELLOW: (control_registry.YELLOW, ON),
    LED_YELLOW_BLINK: (control_registry.YELLOW, BLINK),
}

#: Button velocity -> mode. The trap worth naming: 1 and 2 are legal in BOTH
#: vocabularies and mean different things. On a pad, 1 is solid green and 2 is
#: blinking green; on a button, 1 is "on" in the button's one colour and 2 is
#: the firmware blink. Decoding a button velocity through the grid table would
#: read SCENE_LED_BLINK as a request for green — right by accident on the scene
#: column and wrong on the track row, which is red.
_BUTTON_MEANING: dict[int, str] = {0: OFF, 1: ON, 2: BLINK}


def _variant(apc_label: str | None) -> str:
    """Registry variant for a bench label. Same precedence as the resolvers.

    The bench sets `apc_label = "env"` when the notes came from the environment
    rather than from port-name resolution, so this cannot assume it is handed
    one of the two canonical strings.
    """
    label = (apc_label or "").strip().lower()
    return "mk2" if label in ("mk2", "mkii", "2") else "mk1"


class LedCompositor:
    """The only thing in the process that sends a button or pad LED byte."""

    def __init__(self, midi_out, *, apc_label: str | None) -> None:
        self._midi_out = midi_out
        self._apc_label = apc_label
        self._variant = _variant(apc_label)
        self._controls = control_registry.note_index(self._variant)
        self._layers: dict[str, dict[int, int]] = {n: {} for n in _BY_NAME}
        #: What the device was last told. The one diff cache in the process.
        self._on_wire: dict[int, int] = {}
        #: Every layer that has ever had an opinion about a note. The D2
        #: inventory as runtime data — `control_registry.contested_leds()` is
        #: the same question asked of the source.
        self._claims: dict[int, set[str]] = {}
        self._unknown_warned: set[int] = set()
        self._layers[LAYER_BASE] = {
            note: LED_OFF for note in control_registry.lit_notes(self._variant)
        }
        for note in self._layers[LAYER_BASE]:
            self._claims[note] = {LAYER_BASE}

    # -- submitting -------------------------------------------------------

    def submit(self, layer: str, desired: Mapping[int, int | None]) -> int:
        """Merge `desired` into `layer`. `None` means "I have no opinion".

        Returns the number of messages actually sent, which for a steady
        surface is zero: the comparison happens here, once, against what the
        device was told rather than against what any owner believes.
        """
        store = self._store(layer)
        changed: list[int] = []
        for note, velocity in desired.items():
            if velocity is None:
                if store.pop(note, _ABSENT) is not _ABSENT:
                    changed.append(note)
                continue
            velocity = int(velocity)
            if store.get(note, _ABSENT) != velocity:
                store[note] = velocity
                self._claims.setdefault(note, set()).add(layer)
                changed.append(note)
        return self._resolve(changed)

    def replace(self, layer: str, desired: Mapping[int, int | None]) -> int:
        """Make `desired` the layer's whole opinion, dropping anything else.

        For a layer whose extent moves — the hold warning follows the finger,
        and the gesture row is rebound by a bank change. Merging there would
        leave the pad the finger has left still blinking, which is the same
        class of stale light as the one this module removes.
        """
        store = self._store(layer)
        stale = {note: None for note in store if note not in desired}
        if stale:
            desired = {**stale, **desired}
        return self.submit(layer, desired)

    def clear(self, layer: str) -> int:
        """Drop every opinion in `layer` and let what is under it show."""
        store = self._store(layer)
        if not store:
            return 0
        return self.submit(layer, {note: None for note in list(store)})

    def invalidate(self) -> int:
        """Forget what the device shows and re-assert everything.

        The one legitimate use of the old `force=` flags, and the only one: we
        do not know what is on the panel. At startup we never did — a lamp left
        lit by a previous build outlives the process. After a re-enumeration
        what we knew became a lie. Every other `force=` in the old code was a
        writer working around not owning the wire.
        """
        self._on_wire.clear()
        return self._resolve(self._claims)

    # -- reading ----------------------------------------------------------

    @property
    def variant(self) -> str:
        return self._variant

    def believes(self) -> dict[int, int]:
        """What the device was last told, note -> velocity."""
        return dict(self._on_wire)

    def desired(self) -> dict[int, int]:
        """The resolved model: what the device should show right now."""
        out: dict[int, int] = {}
        for note in self._claims:
            velocity = self._winner(note)
            if velocity is not None:
                out[note] = velocity
        return out

    def contention(self) -> dict[int, tuple[str, ...]]:
        """Notes more than one layer has had an opinion about.

        Not a defect by itself — `stop_all_clips` is deliberately claimed by
        `surface` (its scene indicator) and `transport` (the held lamp), and the
        priority order decides. It is a defect when the claimants disagree about
        what a value MEANS, which is what `blink_conflicts` reports.
        """
        return {
            note: tuple(sorted(layers))
            for note, layers in sorted(self._claims.items())
            if len(layers - {LAYER_BASE}) > 1
        }

    def blink_conflicts(self) -> dict[int, tuple[tuple[str, str], ...]]:
        """Controls where two layers mean different things by `blink`.

        `apc.buttons.single_colour` (MEASURED) leaves a scene button three
        states, so a token that means two things costs a third of the panel's
        vocabulary on that control. Deciding WHICH meaning wins is a UI
        judgement and Mitch's eye; making the conflict countable is not.
        """
        out: dict[int, tuple[tuple[str, str], ...]] = {}
        for note, layers in self.contention().items():
            meanings = {
                name: _BY_NAME[name].blink
                for name in layers
                if _BY_NAME[name].blink
            }
            if len(set(meanings.values())) > 1:
                out[note] = tuple(sorted(meanings.items()))
        return out

    # -- the wire ---------------------------------------------------------

    def _store(self, layer: str) -> dict[int, int]:
        try:
            return self._layers[layer]
        except KeyError:
            raise KeyError(
                f"no LED layer {layer!r}. Layers are declared in "
                f"`led_compositor.LAYERS` with a priority and a reason: "
                f"{tuple(_BY_NAME)}"
            ) from None

    def _winner(self, note: int) -> int | None:
        for name in _ORDER:
            velocity = self._layers[name].get(note, _ABSENT)
            if velocity is not _ABSENT:
                return velocity  # type: ignore[return-value]
        return None

    def _resolve(self, notes: Iterable[int]) -> int:
        sent = 0
        for note in sorted(notes):
            velocity = self._winner(note)
            if velocity is None:
                # Nobody has an opinion any more. Leave the lamp as it is
                # rather than inventing one: "dark" is an opinion too, and the
                # base layer is where it belongs.
                continue
            if self._on_wire.get(note, _ABSENT) == velocity:
                continue
            self._check(note, velocity)
            self._on_wire[note] = velocity
            self._midi_out.send_message([NOTE_ON_CH0, note, velocity])
            sent += 1
        return sent

    def _check(self, note: int, velocity: int) -> None:
        """Refuse a colour the control has been measured not to show.

        Runs on the value about to become bytes, which is every value that ever
        reaches the device: an illegal velocity can never equal what is on the
        wire, because nothing illegal ever got there. A value a higher-priority
        layer masks is not a write and is not checked — the compositor guards
        the wire, not the arithmetic behind it.
        """
        control = self._controls.get(note)
        if control is None:
            if note not in self._unknown_warned:
                self._unknown_warned.add(note)
                warnings.warn(
                    f"painting note {note:#04x} on {self._variant}, which no "
                    "control in `control_registry` claims. Its capability "
                    "cannot be checked, so this write is unguarded. Give it a "
                    "registry row.",
                    UnknownControl,
                    stacklevel=2,
                )
            return
        led = control.led
        if led is None:
            raise control_registry.CapabilityViolation(
                f"{control.id} has no lamp in the registry and something just "
                f"painted its note {note:#04x} with velocity {velocity}."
            )
        table = _GRID_MEANING if control.kind == GRID else None
        if table is not None:
            meaning = table.get(velocity)
        else:
            mode = _BUTTON_MEANING.get(velocity)
            meaning = None if mode is None else (None, mode)
        if meaning is None:
            raise control_registry.CapabilityViolation(
                f"{control.id} was asked for velocity {velocity}, which is not "
                f"in the vocabulary for a {control.kind} control. A pad speaks "
                f"`led_table`'s colours; a button speaks 0/1/2 = off/on/blink, "
                f"and the same number means different things on each."
            )
        colour, mode = meaning
        if colour is None:
            # Velocity 0 is "this lamp, dark". There is no colour in that
            # request, so it is checked against the lamp's own — every
            # established lamp declares OFF, and one that does not is a lamp we
            # must not be writing to at all.
            colour = led.colours[0]
        control_registry.check_colour(control.id, colour, mode=mode)
