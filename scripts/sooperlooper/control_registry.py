"""Every physical control on the APC mini, once, with the evidence for it.

One row per control. **If a note number appears anywhere else, that is the bug.**

WHY THIS FILE EXISTS

`apc_panel.py` opens with rule 2: *"No other module defines a note number. They
import from here."* By 2026-08-30 seven constants outside it named nineteen
notes, plus a hardcoded note range in `probe-apc-buttons.py` and a second copy
of the grid note formula in `slot_surface.py`. A rule written in a docstring
cannot fail a build, so it did not.

What that cost, precisely, and it is still costing it as this is written.
`apc_transport.ARROW_NOTES_MK2 = (0x70, 0x71, 0x72, 0x73)` came from recall and
was flagged UNVERIFIED in its own comment. `apc_panel.SCENE_COLUMN_MK2` is
`0x70..0x77`, measured. They are the same four physical buttons, and the bench
event loop resolves the overlap by the order of its `if` statements: the scene
branch runs first and `continue`s, forty-five lines before `handle_arrow` is
ever called. So on the APC mini mk2 plugged in right now —

  * the Up/Down/Left/Right bank buttons do nothing at all;
  * the viewport is pinned at offset 0, so tracks 9-15 of 15 cannot be reached
    from the surface;
  * `handle_arrow` is the only caller of `set_view`, which makes
    `bank_delta_for_arrow`, `PAGE_STEP`, `NUDGE_STEP`, `GridView.scrolled` and
    `MAX_VIEW_OFFSET` dead code in production;
  * and every session start prints `bottom row -> 8 of 15 tracks (Up/Down page
    8, Shift+Left/Right nudge 1)` — a correct-looking line over a feature that
    has never once worked.

One hundred and twenty-six tests covering the APC were green throughout. Not
one of them compared the arrow tuple against the scene column, because no file
held both. That is the whole argument for this module: not tidiness, but that
two claims about one button could not be put beside each other.

THE RULES

1. Note numbers, CC numbers and the grid note formula live HERE. Nowhere else.
   `tests/test_control_registry.py` walks the AST of every APC module and fails
   on a literal outside this file — the enforcement `apc_panel`'s rule 2 never
   had.

2. `notes` is TOTAL over the variants and an absent control is written `None`,
   never left out. "This variant has no such control" and "nobody has filled
   this in yet" are different facts and must not look the same.

3. Capability is cited, not restated. Every `Led` names the `device_facts` ids
   that establish it and this module looks each one up at import, so a renamed
   or deleted fact is an ImportError here rather than a wrong comment
   elsewhere. Before 2026-08-30 five modules cited
   `device_facts.apc.scene.led_colours` — an id that has never existed — and
   nothing noticed, because nothing called the fact base at all. This module is
   its first caller.

4. Refusing a colour goes through `Fact.refuse_with()`. `device_facts` rule 4
   says a VENDOR or INFERRED fact may never be used to tell Mitch that
   something is impossible, so `check_colour` raises only when every supporting
   fact is MEASURED or OWNER and warns otherwise. The button colour facts ARE
   measured — five probe rounds on 2026-08-29, with a positive control — so
   asking for a yellow scene button raises.

5. A claim we know to be wrong is recorded as a claim, in `DISPUTED`. Not
   deleted, not left live. Deleting it loses the warning that stopped the next
   person repeating it; leaving it live is what killed banking.

WHAT IS NOT HERE

Behaviour. This module is data plus the checks that keep the data honest. Who
paints a light and in what order is the compositor's problem (spec §5.3), and
which gesture a press means is the binding table's (§5.2). `owner` below is a
*description* of who receives a control today, under the configuration the
appliance actually runs; making it a *rule* is stage 3.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import device_facts
from device_facts import INFERRED, MEASURED, OWNER, VENDOR  # noqa: F401 — re-exported tiers

# --- the two surfaces -------------------------------------------------------

#: Canonical variant labels. Exactly these two strings, everywhere. The
#: resolvers accept "mkii", "2", "original", "mini" and an empty string as
#: *input*; what comes out and travels downstream is one of these.
VARIANTS: tuple[str, ...] = ("mk1", "mk2")

#: The device on the desk (aconnect client 28, card 3, verified 2026-08-30).
#: Every mk1 row below is unexercised by any hardware here — see `evidence`.
ATTACHED_VARIANT = "mk2"


def _check_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(
            f"{variant!r} is not a canonical variant. Use exactly one of "
            f"{VARIANTS} — resolve the label once and pass it down."
        )

# --- what a control is ------------------------------------------------------

GRID = "grid"
SCENE = "scene"
TRACK = "track"
MODIFIER = "modifier"
BANK = "bank"
FADER = "fader"

KINDS: tuple[str, ...] = (GRID, SCENE, TRACK, MODIFIER, BANK, FADER)

#: Colours a lamp can show. GREEN/RED/YELLOW are `led_table`'s vocabulary.
GREEN = "green"
RED = "red"
YELLOW = "yellow"

#: "This button has a lamp (apc.buttons.all_have_leds, OWNER) and no addressing
#: for it has been established." Not the same as `led=None`, and emphatically
#: not "it has no LED" — that sentence was in five docstrings, was never
#: measured, and was used twice on 2026-08-29 to tell Mitch a request was
#: impossible. A colour request against this warns; it never raises.
UNESTABLISHED = "unestablished"

OFF = "off"
ON = "on"
BLINK = "blink"

#: Modules that may own a control. One each, by name, so a typo is an
#: ImportError rather than an owner nobody notices is absent.
OWNERS: frozenset[str] = frozenset({
    "slot_surface",          # the 8x8 matrix and the scene column, under multigrid
    "track_gesture",         # the clip row, when multigrid is off
    "apc_transport",         # Shift / Stop All / the stale lamp
    "sooperlooper-apc-bench",  # the event loop itself: the shift latch, banking
    "loop_mix",              # the faders
})

#: Nobody claims it. Pressing it does nothing and nothing lights it. Written
#: down because an unclaimed control and an unnoticed one look identical from
#: the outside, and on this surface that has been the expensive kind of bug.
UNOWNED = "unowned"


#: A fifth tier, local to control identity: we have no claim at all. VENDOR
#: means "a document or a memory says X"; UNKNOWN means nothing says anything.
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Evidence:
    """How we know this control's identity on one variant."""

    tier: str        # MEASURED | OWNER | VENDOR | INFERRED | UNKNOWN
    how: str         # one line: how it was established, or what is missing

    def __post_init__(self) -> None:
        if self.tier not in (MEASURED, OWNER, VENDOR, INFERRED, UNKNOWN):
            raise ValueError(f"unknown evidence tier: {self.tier!r}")
        if not self.how.strip():
            raise ValueError("evidence with no explanation is not evidence")

    @property
    def authoritative(self) -> bool:
        return self.tier in device_facts.AUTHORITATIVE


@dataclass(frozen=True)
class Led:
    """What one control's lamp can show, and the facts that establish it."""

    colours: tuple[str, ...]
    modes: tuple[str, ...]
    fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for fid in self.fact_ids:
            device_facts.fact(fid)   # rule 3: a wrong id is an ImportError here
        if not self.fact_ids:
            raise ValueError("an LED capability with no cited fact is a guess")

    @property
    def established(self) -> bool:
        """False when the lamp exists but nothing addresses it."""
        return self.colours != (UNESTABLISHED,)


@dataclass(frozen=True)
class Control:
    """One physical thing you can touch, and everything we know about it."""

    id: str
    kind: str
    #: Total over VARIANTS. `None` is a stated fact: this variant has no such
    #: control, or what it sends is not established. Never omit a variant.
    notes: Mapping[str, int | None]
    #: Per variant, because the mk2 scene column is measured and the mk1 arrows
    #: are recall, and one `established=` string cannot say both. That is
    #: exactly why ARROW_NOTES_MK2 read as authoritative as SCENE_COLUMN_MK2.
    evidence: Mapping[str, Evidence]
    #: Who receives the PRESS. Exactly one module, or UNOWNED. A control can be
    #: unowned and still be lit — `track_select_8` is — which is why this and
    #: `led_writers` are separate questions rather than one field. Conflating
    #: them is half of spec defect D4.
    owner: str
    led: Led | None = None
    #: Faders live in the same table as buttons. Keeping them in `apc_faders`
    #: is how they acquired their own private copy of the variant sniff.
    cc: Mapping[str, int | None] = field(default_factory=dict)
    #: Every module that sends this lamp's bytes TODAY. More than one is spec
    #: defect D2, written down: two writers, one LED, and which one you see
    #: depends on call order in the event loop. Stage 2 puts a compositor
    #: between them and stage 3 reduces this to one name.
    led_writers: tuple[str, ...] = ()
    #: Modules keeping their own copy of this control's INPUT state. More than
    #: one is the four-shift-latches defect: they are fed from different points
    #: of the same loop, with `continue`s between them, so they can disagree.
    contested: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.id}: unknown kind {self.kind!r}")
        if self.owner != UNOWNED and self.owner not in OWNERS:
            raise ValueError(f"{self.id}: unknown owner {self.owner!r}")
        for table, what in ((self.notes, "notes"), (self.evidence, "evidence")):
            missing = set(VARIANTS) - set(table)
            extra = set(table) - set(VARIANTS)
            if missing or extra:
                raise ValueError(
                    f"{self.id}: {what} must be total over {VARIANTS} — "
                    f"missing {sorted(missing)}, unexpected {sorted(extra)}. "
                    "An absent control is written None, never left out."
                )
        for variant, value in list(self.notes.items()) + list(self.cc.items()):
            if value is not None and not 0 <= value <= 127:
                raise ValueError(f"{self.id}/{variant}: {value} is not a 7-bit value")

    def note(self, variant: str) -> int | None:
        return self.notes[variant]


@dataclass(frozen=True)
class Disputed:
    """A note claim we are not acting on, kept so nobody rediscovers it.

    Rule 5. The refuted mk2 arrow tuple is here rather than deleted because a
    deleted claim comes back: it was recalled once from a document nobody can
    now name, and nothing in the repo would stop it being recalled again.
    """

    control_id: str
    variant: str
    claimed: tuple[int, ...]
    source: str
    why_not_live: str
    resolution: str


# --- the grid note formula, which is also a note number ---------------------

GRID_ROWS = 8
GRID_COLS = 8
GRID_NOTE_MIN = 0
GRID_NOTE_MAX = (GRID_ROWS * GRID_COLS) - 1


def grid_note(row: int, col: int) -> int:
    """APC mini grid note: row 0 = BOTTOM, col 0 = LEFT. Notes 0-63.

    The vertical flip is the trap and it is written down once, in `apc_panel`:
    scene buttons ascend in note order downwards while grid rows ascend upwards.
    Anything that re-derives `row * 8 + col` at a call site has re-opened the
    question — `slot_surface.blank()` did, and skipped the range check with it.
    """
    if not 0 <= row < GRID_ROWS or not 0 <= col < GRID_COLS:
        raise ValueError(f"row/col out of range: ({row}, {col})")
    return row * GRID_COLS + col


# --- the controls -----------------------------------------------------------

_CONTROLS: list[Control] = []


def _add(control: Control) -> Control:
    _CONTROLS.append(control)
    return control


_GRID_EVIDENCE = {
    "mk1": Evidence(MEASURED, "in service since 2026-08-16; pad presses and "
                              "LED writes both land where the formula says"),
    "mk2": Evidence(MEASURED, "device_facts.apc.grid.mk2_encoding, 2026-08-28"),
}
_GRID_LED = Led(
    # Three semantic colours because that is `led_table`'s whole vocabulary and
    # both variants carry it. The mk2's 128-entry palette is a superset we do
    # not use; declaring it here would promise mk1 a colour it cannot show.
    colours=(GREEN, RED, YELLOW),
    modes=(OFF, ON, BLINK),
    fact_ids=("apc.grid.mk2_encoding",),
)

for _row in range(GRID_ROWS):
    for _col in range(GRID_COLS):
        _add(Control(
            id=f"grid_r{_row}_c{_col}",
            kind=GRID,
            notes={v: grid_note(_row, _col) for v in VARIANTS},
            evidence=dict(_GRID_EVIDENCE),
            # Under MPE_SL_MULTIGRID=1 — which is what /etc/mpe/mpe.env sets on
            # the appliance, verified 2026-08-30 — SlotSurface.handles() claims
            # every pad it can map, ahead of the gesture layer. With multigrid
            # off, row 0 goes to track_gesture instead and rows 1-7 to nobody.
            owner="slot_surface",
            # SlotSurface paints the matrix, TrackGesture still paints the clip
            # row's LEDs from gesture state, and TransportButtonLeds darkens
            # rows 1-7 wholesale as "not wired until P3" — which is stale by
            # two features. On reconnect the last of those runs after the other
            # two and erases 56 pads, and the private diff caches then make the
            # erasure permanent.
            led_writers=("slot_surface", "track_gesture", "apc_transport"),
            led=_GRID_LED,
        ))

#: Green, and only green. Three states: off, on, blink at velocity 2. The grid's
#: RGB palette indices are sent and simply not honoured.
_SCENE_LED = Led(
    colours=(GREEN,),
    modes=(OFF, ON, BLINK),
    fact_ids=(
        "apc.scene.led_observed",
        "apc.buttons.single_colour",
        "apc.buttons.channel_response",
    ),
)
_SCENE_EVIDENCE = {
    "mk1": Evidence(MEASURED, "device_facts.apc.scene_column.bottom_is_0x59, "
                              "2026-08-27, by direct observation of which "
                              "physical button sends 0x59"),
    "mk2": Evidence(MEASURED, "device_facts.apc.buttons.note_sets, 2026-08-29 "
                              "probe rounds 1-3"),
}

# Index 0 is the TOP button; it launches grid row 7. See apc_panel for the flip.
for _index in range(GRID_ROWS - 1):
    _add(Control(
        id=f"scene_launch_{_index + 1}",
        kind=SCENE,
        notes={"mk1": 0x52 + _index, "mk2": 0x70 + _index},
        evidence=dict(_SCENE_EVIDENCE),
        owner="slot_surface",
        # clear_unwired_surfaces() darkens all eight of these, and the
        # TransportButtonLeds constructor runs *after* the bench paints them —
        # which is why the scene column has been dark since session start under
        # multigrid, invisible because a dark scene button is also what a
        # correct idle one looks like.
        led_writers=("slot_surface", "apc_transport"),
        led=_SCENE_LED,
    ))

_add(Control(
    # The bottom button. Printed "Stop All Clips", but that is a SHIFT layer:
    # pressed alone it is grid row 0's scene launcher, and always has been.
    id="stop_all_clips",
    kind=SCENE,
    notes={"mk1": 0x59, "mk2": 0x77},
    evidence=dict(_SCENE_EVIDENCE),
    # Pressed alone it is row 0's scene launcher and the scene branch takes it.
    # Held with Shift it belongs to apc_transport — but the scene branch runs
    # first and `continue`s, so a bare press never reaches the transport
    # combo's latches at all. That the documented "Shift first" rule survives
    # is luck: it is enforced by the order of `if` statements.
    owner="slot_surface",
    led_writers=("slot_surface", "apc_transport"),
    contested=("apc_transport",),
    led=_SCENE_LED,
))

#: Identical behaviour to the scene column, in red.
_TRACK_LED = Led(
    colours=(RED,),
    modes=(OFF, ON, BLINK),
    fact_ids=(
        "apc.track.led_observed",
        "apc.buttons.single_colour",
        "apc.buttons.channel_response",
    ),
)

for _index in range(GRID_COLS):
    _add(Control(
        id=f"track_select_{_index + 1}",
        kind=TRACK,
        notes={v: 0x64 + _index for v in VARIANTS},
        evidence={
            "mk1": Evidence(VENDOR, "recall; the mk1 track row has never been "
                                    "captured. apc_transport also claims 0x37 "
                                    "for button 8 — see DISPUTED"),
            "mk2": Evidence(MEASURED, "device_facts.apc.buttons.note_sets, "
                                      "2026-08-29 probe rounds 1-3"),
        },
        # Nothing in the bench reads a track-select press. The note falls
        # through every branch of the event loop and is not even logged, so a
        # wrong note number and a button nobody touched look the same.
        owner=UNOWNED,
        # ...and yet mk2 track 8 IS written: apc_transport sends it OFF, so a
        # lamp left on by an earlier build gets cleared. Never read, never lit,
        # and still has a writer — which is exactly why press ownership and
        # lamp ownership are two columns and not one.
        led_writers=("apc_transport",) if _index == GRID_COLS - 1 else (),
        led=_TRACK_LED,
    ))

_add(Control(
    id="shift",
    kind=MODIFIER,
    notes={"mk1": 0x62, "mk2": 0x7A},
    evidence={
        "mk1": Evidence(MEASURED, "aseqdump capture, SP6 and SP8 2026-08-27: "
                                  "Shift alone sends 0x62 and nothing else"),
        "mk2": Evidence(MEASURED, "device_facts.apc.buttons.note_sets"),
    },
    owner="sooperlooper-apc-bench",
    # Nothing lights Shift. Not a policy — see the Led below.
    led_writers=(),
    # Four independent "is Shift down" latches, fed from different points of
    # the same event loop with `continue`s between them: the bench's own
    # `shift_held`, Mk1ShiftGhostFilter._shift_down, ShiftHoldCombo._shift_down
    # and TransportButtonLeds._shift_down. They can disagree, and on mk2 they
    # do — a bare Stop All press is swallowed by the scene branch, so two of
    # them never learn the button went down.
    contested=("apc_transport",),
    led=Led(
        # OPEN, and open AGAINST expectation. Shift did not light on any of the
        # sixteen channels at velocity 1 or 127. Mitch owns the device and says
        # every button has a lamp, so this is NOT closed as "no LED" — the live
        # hypothesis is that the lamp is firmware-owned and not addressable
        # over MIDI. Do not build anything that depends on lighting it.
        colours=(UNESTABLISHED,),
        modes=(),
        fact_ids=("apc.buttons.all_have_leds", "apc.shift.led"),
    ),
))

for _index, _direction in enumerate(("up", "down", "left", "right")):
    _add(Control(
        id=f"bank_{_direction}",
        kind=BANK,
        notes={
            # Recall, unverified, colliding with nothing on mk1.
            "mk1": 0x40 + _index,
            # UNKNOWN. The recalled 0x70-0x73 are scene buttons 1-4 — see
            # DISPUTED. Guessing again is what produced this row.
            "mk2": None,
        },
        evidence={
            "mk1": Evidence(VENDOR, "recall, never captured — "
                                    "device_facts.apc.bank_arrows.notes"),
            "mk2": Evidence(UNKNOWN, "the only claim ever made was refuted by "
                                     "device_facts.apc.buttons.note_sets; "
                                     "needs --dump-midi and Mitch's fingers"),
        },
        owner="sooperlooper-apc-bench",
        led=Led(
            colours=(UNESTABLISHED,),
            modes=(),
            fact_ids=("apc.buttons.all_have_leds",),
        ),
    ))

for _index in range(GRID_COLS):
    _add(Control(
        id=f"fader_{_index + 1}",
        kind=FADER,
        notes={v: None for v in VARIANTS},
        cc={v: 48 + _index for v in VARIANTS},
        evidence={
            v: Evidence(VENDOR, "believed 48-55 left to right on both "
                                "variants, never confirmed — "
                                "device_facts.apc.faders.ccs")
            for v in VARIANTS
        },
        owner="loop_mix",
        # A fader has no lamp. `led=None` means exactly that and is used only
        # here; a button with an unaddressable lamp is UNESTABLISHED instead.
        led=None,
    ))

_add(Control(
    # Not fader index 8 — a different kind of thing, addressing the loop bus
    # rather than a grid column.
    id="fader_master",
    kind=FADER,
    notes={v: None for v in VARIANTS},
    cc={v: 56 for v in VARIANTS},
    evidence={
        v: Evidence(VENDOR, "believed CC 56 on both variants, never confirmed "
                            "— device_facts.apc.faders.ccs")
        for v in VARIANTS
    },
    owner="loop_mix",
    led=None,
))

CONTROLS: dict[str, Control] = {}
for _control in _CONTROLS:
    if _control.id in CONTROLS:
        raise ValueError(f"duplicate control id: {_control.id}")
    CONTROLS[_control.id] = _control


# --- claims we are not acting on --------------------------------------------

DISPUTED: tuple[Disputed, ...] = (
    Disputed(
        control_id="bank_up",
        variant="mk2",
        claimed=(0x70,),
        source="apc_transport.ARROW_NOTES_MK2, recall, flagged UNVERIFIED in "
               "its own comment and in README.md since it was written",
        why_not_live="0x70 is scene launch 1 — MEASURED 2026-08-29, "
                     "device_facts.apc.buttons.note_sets. Carrying it as a "
                     "live claim is what made banking dead on the mk2.",
        resolution="--dump-midi, press Up, record at MEASURED",
    ),
    Disputed(
        control_id="bank_down",
        variant="mk2",
        claimed=(0x71,),
        source="apc_transport.ARROW_NOTES_MK2",
        why_not_live="0x71 is scene launch 2 — apc.buttons.note_sets",
        resolution="--dump-midi, press Down, record at MEASURED",
    ),
    Disputed(
        control_id="bank_left",
        variant="mk2",
        claimed=(0x72,),
        source="apc_transport.ARROW_NOTES_MK2",
        why_not_live="0x72 is scene launch 3 — apc.buttons.note_sets",
        resolution="--dump-midi, press Left, record at MEASURED",
    ),
    Disputed(
        control_id="bank_right",
        variant="mk2",
        claimed=(0x73,),
        source="apc_transport.ARROW_NOTES_MK2",
        why_not_live="0x73 is scene launch 4 — apc.buttons.note_sets",
        resolution="--dump-midi, press Right, record at MEASURED",
    ),
    Disputed(
        control_id="track_select_8",
        variant="mk1",
        claimed=(0x37,),
        source="apc_transport.NOTE_TRACK8_MK1, which has no readers at all",
        why_not_live="0x37 is grid row 6 col 7 — admitted in its own comment. "
                     "apc_panel says the mk1 track row is 0x64-0x6B, which is "
                     "also what the mk2 row measures at. Neither claim has "
                     "evidence and reasoning has produced three wrong answers "
                     "about this panel already, so the contradiction is "
                     "recorded rather than resolved.",
        resolution="--dump-midi on an mk1, press track button 8",
    ),
)

#: mk1 Track Select 1-8 are believed to *share* notes with grid row 6 rather
#: than having notes of their own — which is why they are not registered as
#: controls: 0x30-0x37 already belong to eight grid pads. Named here so the
#: ghost filter has something to import that says what it actually is.
MK1_TRACK_STATUS_NOTES: tuple[int, ...] = tuple(
    grid_note(GRID_ROWS - 2, col) for col in range(GRID_COLS)
)

for _d in DISPUTED:
    if _d.control_id not in CONTROLS:
        raise ValueError(f"DISPUTED names an unknown control: {_d.control_id}")
    _live = CONTROLS[_d.control_id].notes[_d.variant]
    if _live is not None and _live in _d.claimed:
        raise ValueError(
            f"{_d.control_id}/{_d.variant}: {_live:#04x} is recorded both as "
            "live and as disputed. Pick one — a claim cannot be acted on and "
            "warned about at the same time."
        )


# --- the invariant that has to hold ----------------------------------------

def collisions(claims: Mapping[str, Iterable[int]]) -> dict[int, tuple[str, ...]]:
    """Notes claimed by more than one control, every claimant named.

    Pure and takes its input, so it can be run against claims that are not in
    the registry — which is the only way to show it would have caught the
    arrow/scene overlap. A detector that has never seen a collision is not a
    detector.
    """
    by_note: dict[int, list[str]] = {}
    for control_id, notes in claims.items():
        for note_number in notes:
            by_note.setdefault(note_number, []).append(control_id)
    return {
        n: tuple(sorted(ids))
        for n, ids in sorted(by_note.items())
        if len(ids) > 1
    }


def note_claims(variant: str) -> dict[str, tuple[int, ...]]:
    """{control_id: notes} for one variant — what the registry claims today."""
    _check_variant(variant)
    return {
        c.id: (c.notes[variant],)
        for c in CONTROLS.values()
        if c.notes[variant] is not None
    }


def note_claims_including_disputed(variant: str) -> dict[str, tuple[int, ...]]:
    """`note_claims` with every DISPUTED claim for that variant reinstated.

    This is the shape the repo was in before 2026-08-30, and it is what the
    collision test runs the detector against to prove the detector works.
    """
    claims = note_claims(variant)
    for d in DISPUTED:
        if d.variant != variant:
            continue
        claims[d.control_id] = tuple(claims.get(d.control_id, ())) + d.claimed
    return claims


def assert_no_collisions(claims: Mapping[str, Iterable[int]], variant: str) -> None:
    """Refuse a claim table where one note has two owners.

    Called at import below, so a colliding row cannot reach a test — it cannot
    reach anything, including the appliance. That is the difference between
    this and `apc_panel`'s rule 2, which was true, correct and unenforced.
    """
    clash = collisions(claims)
    if clash:
        raise ValueError(
            f"two controls claim one note on {variant}: "
            + "; ".join(f"{n:#04x} -> {', '.join(ids)}" for n, ids in clash.items())
            + ". One physical button sends one note, so one of these is a "
              "guess. Measure it — do not pick, and do not let the event loop "
              "pick for you by the order of its `if` statements."
        )


for _variant in VARIANTS:
    assert_no_collisions(note_claims(_variant), _variant)


# --- capability, enforced at the boundary -----------------------------------

class CapabilityViolation(ValueError):
    """A colour was asked for that the control has been MEASURED not to show."""


class CapabilityUnmeasured(UserWarning):
    """A colour outside a capability that rests on a document, not a device."""


def check_colour(control_id: str, colour: str, *, mode: str = ON) -> None:
    """Raise or warn if `control_id` cannot show `colour` in `mode`.

    The split is `device_facts` rule 4 and it is the whole point of the
    function. Raising on an unmeasured capability would let this code tell
    Mitch his device cannot do something on the strength of a manufacturer's
    PDF — which is exactly what happened on 2026-08-29, twice, and is why the
    fact base exists. So the refusal is gated on `Fact.refuse_with()`: if any
    supporting fact is VENDOR or INFERRED the call warns and returns, and only
    an all-MEASURED/OWNER capability may refuse.
    """
    control = CONTROLS.get(control_id)
    if control is None:
        raise KeyError(f"no control {control_id!r} in the registry")
    led = control.led
    if led is None:
        raise CapabilityViolation(
            f"{control_id} is a {control.kind} and the registry records no "
            "lamp for it. That is the registry's claim, not a measurement — "
            "if you believe it has one, measure it and record a fact."
        )
    if not led.established:
        warnings.warn(
            f"{control_id}: asked for {colour}/{mode}, but no addressing for "
            f"its lamp is established ({', '.join(led.fact_ids)}). Not "
            "refused: the button has an LED and we do not know how to reach "
            "it, which is a different sentence from 'it cannot'.",
            CapabilityUnmeasured,
            stacklevel=2,
        )
        return
    if colour in led.colours and mode in led.modes:
        return
    try:
        for fid in led.fact_ids:
            device_facts.fact(fid).refuse_with()
    except device_facts.NotMeasured as why:
        warnings.warn(
            f"{control_id}: {colour}/{mode} is outside its declared "
            f"capability {led.colours}/{led.modes}, but that capability is "
            f"not measured, so this is a warning and not a refusal. {why}",
            CapabilityUnmeasured,
            stacklevel=2,
        )
        return
    raise CapabilityViolation(
        f"{control_id} cannot show {colour}/{mode}. It does "
        f"{'/'.join(led.colours)} in {'/'.join(led.modes)}, measured on this "
        f"appliance — see {', '.join(led.fact_ids)}. All colour-carrying UI "
        "has to live on the 8x8 grid."
    )


# --- lookups ----------------------------------------------------------------

def control(control_id: str) -> Control:
    return CONTROLS[control_id]


def note(control_id: str, variant: str) -> int | None:
    _check_variant(variant)
    return CONTROLS[control_id].notes[variant]


def required_note(control_id: str, variant: str) -> int:
    """The note, refusing to hand back `None` as if it were one.

    `None` compared against an incoming note number is silently False forever,
    which is how a control with no note number becomes a control nobody can
    press and nobody notices.
    """
    value = note(control_id, variant)
    if value is None:
        raise LookupError(
            f"{control_id} has no established note on {variant} — "
            f"{CONTROLS[control_id].evidence[variant].how}"
        )
    return value


def cc(control_id: str, variant: str) -> int | None:
    _check_variant(variant)
    return CONTROLS[control_id].cc.get(variant)


def controls_of_kind(kind: str) -> tuple[Control, ...]:
    return tuple(c for c in CONTROLS.values() if c.kind == kind)


def notes_for_kind(kind: str, variant: str) -> tuple[int, ...]:
    """Every established note of one kind, in registry order."""
    _check_variant(variant)
    return tuple(
        c.notes[variant] for c in controls_of_kind(kind) if c.notes[variant] is not None
    )


def control_for_note(note_number: int, variant: str) -> Control | None:
    _check_variant(variant)
    for c in CONTROLS.values():
        if c.notes[variant] == note_number:
            return c
    return None


def scene_column_notes(variant: str) -> tuple[int, ...]:
    """The eight right-hand buttons, TOP to BOTTOM. Stop All is the last one."""
    return notes_for_kind(SCENE, variant)


def track_button_notes(variant: str) -> tuple[int, ...]:
    """The bottom row of eight, LEFT to RIGHT."""
    return notes_for_kind(TRACK, variant)


def arrow_notes(variant: str) -> tuple[int, ...]:
    """Up, down, left, right — or empty when they are not all established.

    Empty rather than partial on purpose: half a bank layer is worse than
    none, because three arrows that work and one that does nothing reads as a
    broken button rather than as an unmapped feature.
    """
    notes = tuple(
        CONTROLS[f"bank_{d}"].notes[variant] for d in ("up", "down", "left", "right")
    )
    return () if any(n is None for n in notes) else notes


def fader_ccs(variant: str) -> tuple[tuple[int, ...], int]:
    """((eight loop faders, left to right), master)."""
    _check_variant(variant)
    loop = tuple(
        CONTROLS[f"fader_{i + 1}"].cc[variant] for i in range(GRID_COLS)
    )
    return loop, CONTROLS["fader_master"].cc[variant]


def unowned() -> tuple[Control, ...]:
    """Controls nothing claims. The work queue for stage 3."""
    return tuple(c for c in CONTROLS.values() if c.owner == UNOWNED)


def contested() -> tuple[Control, ...]:
    """Controls whose input state is latched in more than one place."""
    return tuple(c for c in CONTROLS.values() if c.contested)


def contested_leds() -> tuple[Control, ...]:
    """Lamps with more than one writer today — spec defect D2, enumerated.

    The compositor (stage 2) is what empties this: owners submit desired state
    and exactly one thing talks to the wire, so two writers cannot exist.
    """
    return tuple(c for c in CONTROLS.values() if len(c.led_writers) > 1)


def unmeasured_controls(variant: str) -> tuple[Control, ...]:
    """Controls whose identity on `variant` rests on a document or on nothing.

    Read this before promising anything about the panel. It is the control-level
    twin of `device_facts.unmeasured()`, and for the same reason.
    """
    _check_variant(variant)
    return tuple(
        c for c in CONTROLS.values() if not c.evidence[variant].authoritative
    )
