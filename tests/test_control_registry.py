"""The invariants the control registry exists to make failable.

Every test here is named for a specific defect that shipped, and every one of
them would have caught it. That is the bar charter §2 sets: *"a test whose
failure would not have caught the bug it is named for is a test that needs
rewriting, even if it passes today."*

The defects, in order:

  * `ARROW_NOTES_MK2 = (0x70,0x71,0x72,0x73)` inside `SCENE_COLUMN_MK2 =
    0x70..0x77`. Banking dead on the attached mk2, tracks 9-15 unreachable,
    126 green APC tests over it, and a startup banner advertising the feature.
  * Seven note-defining constants outside `apc_panel.py` naming nineteen
    notes, under a docstring rule saying there should be none.
  * A grid note formula re-derived in `slot_surface.blank()`, without the
    range check the real one has.
  * A capability rule the spec called "executable rather than aspirational"
    that had never executed, because nothing in the repo called `device_facts`.
"""

from tests import conftest  # noqa: F401 — bare sooperlooper imports

import ast
import re
import unittest
import warnings
from pathlib import Path

from scripts.sooperlooper import control_registry as reg
from scripts.sooperlooper import device_facts

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "scripts" / "sooperlooper-apc-bench.py"

#: Every module that speaks to the APC. The registry is the one exemption.
APC_SOURCES = sorted(
    set((REPO / "scripts" / "sooperlooper").glob("*.py"))
    | set((REPO / "scripts").glob("*apc*.py"))
)


class NoteCollisionTests(unittest.TestCase):
    """Two controls may not claim one note on one variant.

    One physical button sends one note. When two claims exist the event loop
    resolves them by the order of its `if` statements, which is not a decision
    anybody made and cannot be read off either claim.
    """

    def test_no_two_controls_claim_one_note_per_variant(self) -> None:
        for variant in reg.VARIANTS:
            with self.subTest(variant=variant):
                clash = reg.collisions(reg.note_claims(variant))
                self.assertEqual(
                    clash,
                    {},
                    "\n".join(
                        f"{note:#04x} claimed by {', '.join(ids)}"
                        for note, ids in clash.items()
                    ),
                )

    def test_the_collision_check_catches_the_arrow_scene_overlap(self) -> None:
        """Proof the check above is not vacuous.

        It passes today because the refuted claim was taken out of the live
        table. Reinstate it — which is the shape the repo was in until
        2026-08-30 — and the detector has to name both claimants for all four
        buttons. A detector that has never seen a collision is not a detector.
        """
        clash = reg.collisions(reg.note_claims_including_disputed("mk2"))
        self.assertEqual(
            clash,
            {
                0x70: ("bank_up", "scene_launch_1"),
                0x71: ("bank_down", "scene_launch_2"),
                0x72: ("bank_left", "scene_launch_3"),
                0x73: ("bank_right", "scene_launch_4"),
            },
        )

    def test_the_registry_refuses_a_colliding_row_at_import(self) -> None:
        """The check that runs at import, run against a colliding row.

        `assert_no_collisions` is called once per variant at the bottom of the
        module, so a row like this cannot reach a test — it cannot reach the
        appliance either. That is the whole difference between this and
        `apc_panel`'s rule 2, which was true, correct and unenforced.
        """
        colliding = dict(reg.note_claims("mk2"), bank_up=(0x70,))
        with self.assertRaises(ValueError) as caught:
            reg.assert_no_collisions(colliding, "mk2")
        message = str(caught.exception)
        self.assertIn("bank_up", message)
        self.assertIn("scene_launch_1", message)
        # And it still passes for the table as it actually ships.
        reg.assert_no_collisions(reg.note_claims("mk2"), "mk2")

    def test_the_refuted_mk2_arrow_claim_is_recorded_not_deleted(self) -> None:
        """Rule 5: a wrong claim stays visible, or it comes back.

        0x70-0x73 was recalled once from a source nobody can now name. Deleting
        it would leave nothing in the repo to stop it being recalled again.
        """
        disputed = {
            d.control_id: d for d in reg.DISPUTED
            if d.variant == "mk2" and d.control_id.startswith("bank_")
        }
        self.assertEqual(
            sorted(disputed),
            ["bank_down", "bank_left", "bank_right", "bank_up"],
        )
        for control_id, d in disputed.items():
            with self.subTest(control=control_id):
                self.assertIn(d.claimed[0], reg.scene_column_notes("mk2"))
                self.assertIsNone(reg.note(control_id, "mk2"))
                self.assertTrue(d.resolution.strip())


class NoteLiteralTests(unittest.TestCase):
    """No note literal outside the registry.

    `apc_panel.py` has carried this as rule 2 since 2026-08-27 and by
    2026-08-30 seven constants outside it named nineteen notes. The difference
    between then and now is only that this fails a build.

    What the instrument can and cannot see, stated plainly so nobody mistakes
    a pass for a proof: it reads the AST of the modules below, so a note built
    at runtime from arithmetic it cannot follow gets through, and so does a
    button-space note written in decimal outside a note-named binding. It
    catches every form the seven historical constants took.
    """

    #: Names that hold note or CC numbers. Deliberately wide.
    NOTE_NAME = re.compile(r"(^|_)NOTES?($|_)|_MK[12]$")

    #: Exempted by name and reason — never by file. Each line is a claim that
    #: this particular constant is not a note number.
    EXEMPT_NAMES = {
        # A SysEx *mode* byte whose label happens to be "Notes" (the pad mode).
        # Nothing addresses a note with it.
        ("apc_mode.py", "MODE_NOTES"),
        # The note-on STATUS byte, 0x90 — a message type, not a control. It is
        # named for the MIDI message because that is what it is, and there is
        # exactly one of it in the process now that the compositor is the only
        # writer. What it encodes is `device_facts.apc.buttons.channel_response`
        # (MEASURED 2026-08-29): the channel axis is exhausted, and 0x90 is the
        # only channel a button LED answers on.
        ("led_compositor.py", "NOTE_ON_CH0"),
    }
    EXEMPT_HEX = {
        # SysEx identity bytes. 0x62 is also mk1 Shift, which is a coincidence
        # of the protocol and not a second home for the note.
        ("apc_mode.py", "AKAI_MANUFACTURER_ID"),
        ("apc_mode.py", "APC_MINI_MK2_PRODUCT"),
        ("apc_mode.py", "MODE_MESSAGE_TYPE"),
        # The 7-bit ceiling in a range check, not a control.
        ("apc_mode.py", "parse_mode_sysex"),
    }

    def _sources(self):
        for path in APC_SOURCES:
            if path.name == "control_registry.py":
                continue   # the one home
            yield path, path.read_text(encoding="utf-8")

    def test_no_note_named_constant_holds_a_literal(self) -> None:
        offenders = []
        for path, src in self._sources():
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    value = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names, value = [node.target.id], node.value
                else:
                    continue
                if value is None:
                    continue
                hit = [n for n in names if self.NOTE_NAME.search(n)]
                if not hit:
                    continue
                if any((path.name, n) in self.EXEMPT_NAMES for n in hit):
                    continue
                for literal in _int_constants(value):
                    offenders.append(
                        f"{path.name}:{node.lineno} {'/'.join(hit)} = "
                        f"...{literal}..."
                    )
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_button_note_written_in_hex_outside_the_registry(self) -> None:
        """Catches the forms a name-based rule misses.

        `probe-apc-buttons.py` carried `list(range(0x64, 0x6C))` inline — the
        only statement in the repo of what "the track row" in
        `device_facts.apc.track.led_observed` actually meant, and itself an
        uncited note literal.
        """
        offenders = []
        for path, src in self._sources():
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, int):
                    continue
                if isinstance(node.value, bool) or not 0x40 <= node.value <= 0x7F:
                    continue
                segment = (ast.get_source_segment(src, node) or "").lower()
                if not segment.startswith("0x"):
                    continue   # decimal here is a size or a clamp, not a note
                if self._exempt_hex(path, tree, node):
                    continue
                offenders.append(f"{path.name}:{node.lineno} {segment}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def _exempt_hex(self, path, tree, node) -> bool:
        for enclosing in ast.walk(tree):
            names = ()
            if isinstance(enclosing, ast.Assign):
                names = tuple(t.id for t in enclosing.targets if isinstance(t, ast.Name))
            elif isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = (enclosing.name,)
            for name in names:
                if (path.name, name) not in self.EXEMPT_HEX:
                    continue
                if any(child is node for child in ast.walk(enclosing)):
                    return True
        return False

    def test_the_grid_note_formula_has_one_home(self) -> None:
        """`row * 8 + col` may only be written in `control_registry.grid_note`.

        `slot_surface.blank()` was the third copy, next to an imported
        GRID_ROWS and a hardcoded 8, and it was the one without the range
        check — so it could address a note the grid does not have and say
        nothing.
        """
        offenders = []
        for path, src in self._sources():
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
                    continue
                left = node.left
                if not isinstance(left, ast.BinOp) or not isinstance(left.op, ast.Mult):
                    continue
                # Either spelling of the grid width. Writing it as GRID_COLS is
                # tidier and just as much a second home for the formula.
                factors = [left.left, left.right]
                if not any(
                    (isinstance(f, ast.Constant) and f.value == reg.GRID_COLS)
                    or (isinstance(f, ast.Name) and f.id == "GRID_COLS")
                    for f in factors
                ):
                    continue
                offenders.append(
                    f"{path.name}:{node.lineno} re-derives the grid note "
                    "formula — call control_registry.grid_note / pad_note"
                )
        self.assertEqual(offenders, [], "\n".join(offenders))


def _int_constants(value: ast.AST):
    """Int literals in an expression, ignoring subscript indices.

    `_MK2_CCS[0]` is an index into a tuple the registry owns, not a note.
    """
    skip = set()
    for node in ast.walk(value):
        if isinstance(node, ast.Subscript):
            for sub in ast.walk(node.slice):
                skip.add(id(sub))
    for node in ast.walk(value):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and id(node) not in skip
        ):
            yield node.value


class ReachabilityTests(unittest.TestCase):
    """Every registered control reaches its declared owner.

    This is the test that says "this button does nothing" without any
    hardware, and it is the sentence nobody was able to write for six weeks.
    T1 and T2 catch the arrow collision as a data error; this catches it as
    what the player experiences.
    """

    def _dispatch(self, note: int, variant: str) -> list[str]:
        """Who sees a note-on for `note`, under the live configuration.

        Live means `MPE_SL_MULTIGRID=1` — what `/etc/mpe/mpe.env` sets on the
        appliance, verified 2026-08-30, against a code default of "0". The
        single-clip arrangement is deliberately not modelled here: it is not
        what runs, and a model of two configurations that agrees with neither
        is worse than no model.

        **Rewritten 2026-08-30 for charter stage 5 / spec §5.2.** This used to
        be a hand-copied replica of the bench's `if`-chain, pinned to the source
        by a list of nine branch strings in source order — because reachability
        WAS statement order, and there was nowhere else to read it from. Stage 5
        made routing a table, so the model asks the table. That is not the test
        checking itself: `control_registry.Control.owner` and
        `binding_table.ACTIONS[...].owner` are two independent statements, and
        this compares them. The old form could not survive the stage it was
        written to enable — the branch strings it pinned are the ones the stage
        deletes.
        """
        from scripts.sooperlooper import binding_table as bt
        from scripts.sooperlooper.apc_transport import resolve_apc_transport_notes

        port = "APC mini mk2 MIDI 1" if variant == "mk2" else "APC MINI"
        _shift, _stop, label = resolve_apc_transport_notes(port)
        table = bt.for_surface(label, multigrid=True)
        binding = table.resolve(note, layer=bt.BASE, gesture=bt.PRESS)
        if binding is None:
            return []
        # An action owned by nobody is a press that reaches nobody — the eight
        # track-select buttons, whose notes fall through the whole surface.
        return [owner for owner in binding.owners if owner != reg.UNOWNED]

    def test_the_model_is_the_table_the_bench_runs(self) -> None:
        """The model above is worthless if the bench routes some other way.

        Structural, not a substring: the bench must contain a real call to
        `BindingRouter` whose table argument is `for_surface(...)` with the same
        multigrid flag the model assumes. A comment or a docstring mentioning
        either name cannot satisfy this, because comments are not AST nodes —
        which is the failure mode the lifecycle guard hit on 2026-08-30, where a
        substring check passed over deleted code because a comment named it.
        """
        tree = ast.parse(BENCH.read_text(encoding="utf-8"))
        built = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BindingRouter"
        ]
        self.assertEqual(
            len(built), 1,
            "the bench builds exactly one router; the model above describes it",
        )
        table_arg = built[0].args[0]
        self.assertIsInstance(table_arg, ast.Call)
        self.assertEqual(table_arg.func.id, "for_surface")
        self.assertEqual(
            [k.arg for k in table_arg.keywords], ["multigrid"],
            "the table is built per mode; the model assumes multigrid",
        )

    def test_every_control_reaches_its_declared_owner(self) -> None:
        for variant in reg.VARIANTS:
            for control in reg.CONTROLS.values():
                if control.kind == reg.FADER:
                    continue   # CC, not a note; see FaderTests below
                note = control.notes[variant]
                with self.subTest(control=control.id, variant=variant):
                    if note is None:
                        # Unreachable is allowed only where the registry says
                        # why. An absent note with a confident evidence tier
                        # would be a control quietly dropped off the surface.
                        self.assertEqual(
                            control.evidence[variant].tier,
                            reg.UNKNOWN,
                            f"{control.id} has no note on {variant} but its "
                            "evidence does not say the note is unknown",
                        )
                        continue
                    seen = self._dispatch(note, variant)
                    if control.owner == reg.UNOWNED:
                        self.assertEqual(
                            seen, [],
                            f"{control.id} is declared unowned but "
                            f"{seen} receives its press",
                        )
                    else:
                        self.assertIn(
                            control.owner, seen,
                            f"{control.id} ({note:#04x} on {variant}) is owned "
                            f"by {control.owner} but the event loop hands it "
                            f"to {seen or 'nobody'}",
                        )

    def test_the_reachability_model_catches_the_swallowed_arrows(self) -> None:
        """Proof this test would have caught the dead arrows.

        Feed it the notes the mk2 arrows used to claim. Every one is taken by
        the scene branch, which `continue`s forty-five lines before
        `handle_arrow` is reached — so the owner never sees the press and the
        player presses a button that does nothing.
        """
        for note in (0x70, 0x71, 0x72, 0x73):
            with self.subTest(note=hex(note)):
                self.assertEqual(self._dispatch(note, "mk2"), ["slot_surface"])
                self.assertNotIn("sooperlooper-apc-bench", self._dispatch(note, "mk2"))

    def test_the_unowned_controls_are_the_ones_we_think(self) -> None:
        """An unclaimed control and an unnoticed one look identical live.

        All eight track-select buttons send notes nothing in the bench reads:
        the press falls through every branch and is not even logged, so a wrong
        note number and a button nobody touched produce the same silence.
        Track 8 is the sharpest case — it is written (kept dark) and never
        read, which is why press ownership and lamp ownership are two columns.
        """
        self.assertEqual(
            sorted(c.id for c in reg.unowned()),
            sorted(f"track_select_{i}" for i in range(1, 9)),
        )
        # `led_compositor`, not `apc_transport`: the clear moved to the
        # compositor's base layer, which is the lowest priority and therefore
        # cannot erase an owner that has spoken. `apc_transport` used to
        # re-assert it OFF from four separate methods.
        self.assertEqual(
            reg.control("track_select_8").led_writers, ("led_compositor",)
        )

    def test_every_lamp_with_two_writers_is_declared_as_such(self) -> None:
        """Defect D2 as data, so stage 2 has a list and stage 3 has a target.

        Two writers on one LED, resolved by call order in a 150-line event
        loop, is the reason three consecutive LED changes went wrong in three
        different ways. This is the inventory; the compositor empties it.
        """
        two_writers = {c.id: c.led_writers for c in reg.contested_leds()}
        self.assertTrue(two_writers, "the D2 inventory cannot be empty yet")
        for control_id, writers in two_writers.items():
            with self.subTest(control=control_id):
                self.assertIn(reg.control(control_id).owner, writers + (reg.UNOWNED,))
                self.assertIn("apc_transport", writers)


class CapabilityTests(unittest.TestCase):
    """Colour requests are checked against measured capability.

    Spec §5.4 wanted this to raise. Charter §5 narrowed it: raise on
    MEASURED/OWNER, warn on VENDOR/INFERRED — because refusing on a vendor
    document is exactly what `device_facts` rule 4 was written to stop, and
    exactly what happened on 2026-08-29, twice, to Mitch's face.
    """

    def test_a_yellow_scene_button_raises(self) -> None:
        with self.assertRaises(reg.CapabilityViolation) as caught:
            reg.check_colour("scene_launch_4", reg.YELLOW)
        self.assertIn("green", str(caught.exception))
        self.assertIn("apc.scene.led_observed", str(caught.exception))

    def test_a_red_stop_all_raises(self) -> None:
        """The request that produced the stale-lamp workaround.

        "Stop All should blink red" was answered by moving the warning onto a
        track button, which lit the wrong control and took the blink off the
        one the player was holding. The correct answer was "this hardware
        cannot do it", said out loud — which this now says, on measured
        grounds, at the point of the request.
        """
        with self.assertRaises(reg.CapabilityViolation):
            reg.check_colour("stop_all_clips", reg.RED)

    def test_green_on_a_scene_button_is_fine_in_every_mode(self) -> None:
        for mode in (reg.OFF, reg.ON, reg.BLINK):
            with self.subTest(mode=mode):
                reg.check_colour("scene_launch_1", reg.GREEN, mode=mode)

    def test_the_grid_takes_every_colour_we_have(self) -> None:
        for colour in (reg.GREEN, reg.RED, reg.YELLOW):
            with self.subTest(colour=colour):
                reg.check_colour("grid_r0_c0", colour, mode=reg.BLINK)

    def test_shift_warns_and_never_refuses(self) -> None:
        """Rule 4, at the one control where it still bites.

        Nothing has lit Shift on any channel at any velocity. Mitch owns the
        device and says every button has an LED, so the honest sentence is "we
        do not know how to reach it", not "it cannot" — and this must never
        become a refusal by accident.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reg.check_colour("shift", reg.GREEN)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, reg.CapabilityUnmeasured)
        self.assertIn("apc.buttons.all_have_leds", str(caught[0].message))

    def test_an_unmeasured_capability_warns_instead_of_raising(self) -> None:
        """The branch charter §5 exists for.

        Build a control whose capability rests on a VENDOR fact and ask it for
        a colour outside that capability. It must warn — `Fact.refuse_with()`
        is the gate, and a VENDOR fact does not open it.
        """
        vendor_led = reg.Led(
            colours=(reg.GREEN,),
            modes=(reg.ON,),
            fact_ids=("apc.bank_arrows.notes",),   # VENDOR
        )
        self.assertFalse(device_facts.fact("apc.bank_arrows.notes").authoritative)
        probe = reg.Control(
            id="_capability_probe",
            kind=reg.SCENE,
            notes={v: None for v in reg.VARIANTS},
            evidence={v: reg.Evidence(reg.VENDOR, "test fixture") for v in reg.VARIANTS},
            owner=reg.UNOWNED,
            led=vendor_led,
        )
        reg.CONTROLS[probe.id] = probe
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                reg.check_colour(probe.id, reg.RED)
            self.assertEqual(len(caught), 1)
            self.assertIs(caught[0].category, reg.CapabilityUnmeasured)
            self.assertIn("not measured", str(caught[0].message))
        finally:
            del reg.CONTROLS[probe.id]

    def test_every_cited_fact_id_exists(self) -> None:
        """Rule 3, and the reason the registry is the fact base's first caller.

        Five modules cited `device_facts.apc.scene.led_colours` — an id that
        has never existed — and nothing noticed for a day, because prose cannot
        fail a build. Here a wrong id is an ImportError before any of this runs;
        this asserts the lookup is real rather than decorative.
        """
        cited = {
            fid
            for control in reg.CONTROLS.values()
            if control.led is not None
            for fid in control.led.fact_ids
        }
        self.assertTrue(cited)
        for fid in sorted(cited):
            with self.subTest(fact=fid):
                self.assertIs(device_facts.fact(fid), device_facts.FACTS[fid])
        with self.assertRaises(KeyError):
            reg.Led(colours=(reg.GREEN,), modes=(reg.ON,),
                    fact_ids=("apc.scene.led_colours",))


class FactCitationTests(unittest.TestCase):
    """Every `device_facts.<id>` written in prose names a fact that exists.

    On 2026-08-29 five modules acquired citations of
    `device_facts.apc.scene.led_colours` and `.apc.track.led_colours`. Neither
    id has ever existed. Five bad citations in a single day is what a fact base
    with no callers predicts — prose cannot fail a build. This makes it fail.
    """

    CITATION = re.compile(r"device_facts\.((?:[a-z0-9_]+\.)+[a-z0-9_]+)")

    #: Module attributes, not fact ids. Named individually so the exemption
    #: cannot quietly widen to cover a real citation.
    NOT_FACT_IDS = frozenset({
        "py", "FACTS", "fact", "record", "unmeasured", "AUTHORITATIVE",
        "NotMeasured", "Fact.refuse_with", "refuse_with",
        "RESOLUTION_PATH_UNMEASURED_CONTROLS",
    })

    #: Ids that never existed, named on purpose where the history is told.
    #: `test_the_retired_ids_really_are_absent` proves this list cannot shadow
    #: a real fact, so exempting them costs nothing.
    RETIRED_IDS = frozenset({"apc.scene.led_colours", "apc.track.led_colours"})

    def test_every_cited_fact_id_exists(self) -> None:
        sources = list(APC_SOURCES) + [BENCH, Path(__file__)]
        bad = []
        seen = 0
        for path in sources:
            for cited in self.CITATION.findall(path.read_text(encoding="utf-8")):
                if cited in self.NOT_FACT_IDS or cited.split(".")[-1] in self.NOT_FACT_IDS:
                    continue
                if cited.removeprefix("apc.") in self.RETIRED_IDS or cited in self.RETIRED_IDS:
                    continue
                seen += 1
                if cited not in device_facts.FACTS:
                    bad.append(
                        f"{path.name}: device_facts.{cited} — no such fact. "
                        "Cite an id that exists, or record the fact."
                    )
        self.assertGreater(seen, 0, "the citation regex found nothing to check")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_the_retired_ids_really_are_absent(self) -> None:
        for retired in self.RETIRED_IDS:
            with self.subTest(fact=retired):
                self.assertNotIn(retired, device_facts.FACTS)

    #: A fact id broken over two source lines, e.g. a string literal ending
    #: `...apc.buttons."` continued by `"note_sets"` on the next line. It reads
    #: correctly and is invisible to grep and to the check above alike — the
    #: same class of defect as writing 0x7A as 122. Two of these existed when
    #: this file was written; both were unwrapped rather than exempted.
    SPLIT_CITATION = re.compile(r"device_facts\.[a-z0-9_.]*\.[\"']\s*$", re.M)

    def test_no_fact_id_is_split_across_two_lines(self) -> None:
        for path in list(APC_SOURCES) + [BENCH]:
            with self.subTest(path=path.name):
                self.assertIsNone(
                    self.SPLIT_CITATION.search(path.read_text(encoding="utf-8")),
                    "a fact id broken over two lines is a citation nothing can "
                    "look up — keep the whole id on one line",
                )


class FactBaseWorkQueueTests(unittest.TestCase):
    """`device_facts.unmeasured()` is the work queue, and it must not lie.

    Until 2026-08-30 it returned `[]`, and the charter read that as "there is
    nothing left to measure". The list was empty because the two open
    questions had never been written down at all — the arrows and the fader
    CCs bypassed the fact base entirely, which is how an unmeasured guess
    stayed load-bearing for shipped behaviour without ever passing rule 4.

    An empty queue is a claim. This is the caller that checks it.
    """

    def test_the_queue_is_exactly_the_two_open_questions(self) -> None:
        self.assertEqual(
            [f.id for f in device_facts.unmeasured()],
            ["apc.bank_arrows.notes", "apc.faders.ccs"],
        )
        self.assertEqual(
            sorted(device_facts.RESOLUTION_PATH_UNMEASURED_CONTROLS),
            sorted(f.id for f in device_facts.unmeasured()),
        )

    def test_nothing_unmeasured_can_refuse_a_request(self) -> None:
        """Rule 4, exercised on every non-authoritative fact there is."""
        for f in device_facts.unmeasured():
            with self.subTest(fact=f.id):
                with self.assertRaises(device_facts.NotMeasured):
                    f.refuse_with()

    def test_every_authoritative_fact_may_refuse(self) -> None:
        for f in device_facts.FACTS.values():
            if not f.authoritative:
                continue
            with self.subTest(fact=f.id):
                f.refuse_with()   # must not raise


class RegistryShapeTests(unittest.TestCase):
    """The data rules that keep the table readable."""

    def test_notes_are_total_over_the_variants(self) -> None:
        """`None` is a stated fact; a missing key is an oversight.

        A control genuinely absent on a variant and one nobody has filled in
        must not look the same, which is why the table is total.
        """
        for control in reg.CONTROLS.values():
            with self.subTest(control=control.id):
                self.assertEqual(set(control.notes), set(reg.VARIANTS))
                self.assertEqual(set(control.evidence), set(reg.VARIANTS))

    def test_a_partial_note_table_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            reg.Control(
                id="_half_declared",
                kind=reg.SCENE,
                notes={"mk2": 0x00},
                evidence={v: reg.Evidence(reg.VENDOR, "x") for v in reg.VARIANTS},
                owner=reg.UNOWNED,
            )
        self.assertIn("total over", str(caught.exception))

    def test_evidence_carries_a_tier_and_an_explanation(self) -> None:
        for control in reg.CONTROLS.values():
            for variant in reg.VARIANTS:
                with self.subTest(control=control.id, variant=variant):
                    self.assertTrue(control.evidence[variant].how.strip())

    def test_the_mk2_controls_that_rest_on_nothing_are_named(self) -> None:
        """The work queue, kept honest.

        Everything here is a control we ship against a document or a memory.
        It should shrink at the device, never by editing this list.
        """
        self.assertEqual(
            sorted(c.id for c in reg.unmeasured_controls("mk2")),
            sorted(
                ["bank_up", "bank_down", "bank_left", "bank_right", "fader_master"]
                + [f"fader_{i}" for i in range(1, 9)]
            ),
        )

    def test_the_panel_values_are_unchanged_by_the_move(self) -> None:
        """Stage 1 is data only. Same numbers, one home.

        Pinned against literals on purpose: deriving the expectation from the
        registry would make this test agree with any move, including a wrong
        one.
        """
        from scripts.sooperlooper import apc_leds, apc_panel
        from scripts.sooperlooper.apc_faders import resolve_fader_ccs

        self.assertEqual(apc_panel.SCENE_COLUMN_MK1, tuple(range(0x52, 0x5A)))
        self.assertEqual(apc_panel.SCENE_COLUMN_MK2, tuple(range(0x70, 0x78)))
        self.assertEqual(apc_panel.NOTE_STOP_ALL_CLIPS_MK1, 0x59)
        self.assertEqual(apc_panel.NOTE_STOP_ALL_CLIPS_MK2, 0x77)
        self.assertEqual(apc_panel.NOTE_SHIFT_MK1, 0x62)
        self.assertEqual(apc_panel.NOTE_SHIFT_MK2, 0x7A)
        self.assertEqual(apc_panel.TRACK_BUTTON_NOTES_MK1, tuple(range(0x64, 0x6C)))
        self.assertEqual((apc_panel.GRID_NOTE_MIN, apc_panel.GRID_NOTE_MAX), (0x00, 0x3F))
        self.assertEqual((apc_leds.PAD_NOTE_MIN, apc_leds.PAD_NOTE_MAX), (0x00, 0x3F))
        self.assertEqual(reg.MK1_TRACK_STATUS_NOTES, tuple(range(0x30, 0x38)))
        self.assertEqual(
            resolve_fader_ccs("APC mini mk2")[:2], (tuple(range(48, 56)), 56)
        )
        self.assertEqual(reg.grid_note(6, 7), 0x37)


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
