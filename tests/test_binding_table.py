"""Routing is a table, and it is not allowed to move back into the loop.

Charter stage 5, spec §5.2. Every test here is named for a specific defect:

  * **The mk2 bank arrows.** `ARROW_NOTES_MK2` claimed 0x70-0x73; the scene
    branch claimed the same four notes and `continue`d forty-five lines before
    `handle_arrow` was reached. Four buttons did nothing, tracks 9-15 were
    unreachable from the surface, and the boot banner advertised banking on
    every start. 126 green APC tests. Reachability was a property of statement
    order, and nothing read statement order.
  * **The bottom button's two hats.** Whether 0x59/0x77 is a scene launcher or
    Stop All depends on a latch set at ITS OWN press-down. Getting that from
    the live modifier state meant a player who released Shift first left the
    combo holding a button forever.
  * **Four independent "is Shift down" latches**, fed from different points of
    one loop with `continue`s between them.

The guard at the top is the load-bearing one, and it is written against a
lesson from the same night: a substring check is not a guard. A lifecycle test
here passed while the code it guarded had been deleted, because a *comment*
mentioning the name satisfied the match. Everything below matches AST nodes.
Comments and docstrings are not AST nodes, so prose cannot satisfy any of it.
"""

from tests import conftest  # noqa: F401 — bare sooperlooper imports

import ast
import unittest
from pathlib import Path

from scripts.sooperlooper import binding_table as bt
from scripts.sooperlooper import control_registry as reg

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "scripts" / "sooperlooper-apc-bench.py"
TABLE = REPO / "scripts" / "sooperlooper" / "binding_table.py"

#: The name the event loop calls the incoming note/CC number by.
NOTE_VAR = "n"
#: The router. A use of the note anywhere but an argument to a call ON THIS
#: OBJECT is a routing decision that has escaped the table.
ROUTER_VAR = "bindings"


def _event_loop(source: str) -> ast.While:
    """`run_bench`'s `while True:` — the loop every MIDI byte passes through.

    Raises rather than returning None: a guard that silently finds nothing to
    check is the failure this file exists to prevent.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_bench":
            for stmt in ast.walk(node):
                if (
                    isinstance(stmt, ast.While)
                    and isinstance(stmt.test, ast.Constant)
                    and stmt.test.value is True
                ):
                    return stmt
    raise AssertionError(
        "no `while True:` inside run_bench — the guard below has nothing to "
        "guard, which is exactly how it would pass while the thing it protects "
        "is gone"
    )


def _note_uses(loop: ast.While) -> tuple[list[ast.Name], list[ast.Name]]:
    """(allowed, escaped) reads of the note variable inside the loop.

    Allowed is deliberately narrow: an argument to a call on the router, or a
    substitution inside an f-string (a log line cannot route anything). A
    *store* — the `st, n = msg[0], msg[1]` unpack — is not a use.

    Everything else is a routing decision outside the table. That covers a
    re-grown `if`-chain whether it tests the note directly (`n == shift_note`,
    `n in by_note`) or launders it through a local first
    (`scene_row = scene_press_row(n, ...)` — the exact shape that made the
    arrows unreachable), because either way it has to read `n`.
    """
    allowed_nodes: set[int] = set()
    for node in ast.walk(loop):
        if isinstance(node, ast.JoinedStr):
            for name in ast.walk(node):
                if isinstance(name, ast.Name) and name.id == NOTE_VAR:
                    allowed_nodes.add(id(name))
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == ROUTER_VAR
        ):
            continue
        for arg in list(node.args) + [k.value for k in node.keywords]:
            for name in ast.walk(arg):
                if isinstance(name, ast.Name) and name.id == NOTE_VAR:
                    allowed_nodes.add(id(name))

    allowed: list[ast.Name] = []
    escaped: list[ast.Name] = []
    for node in ast.walk(loop):
        if not isinstance(node, ast.Name) or node.id != NOTE_VAR:
            continue
        if not isinstance(node.ctx, ast.Load):
            continue
        (allowed if id(node) in allowed_nodes else escaped).append(node)
    return allowed, escaped


#: The event loop as it stood at `57f6dcd`, trimmed to the note branches and
#: their order. Not a paraphrase — these are the real lines, and their order is
#: the bug. Kept as a fixture rather than read from git so the positive control
#: works in a tarball, on the Pi, and in CI.
HISTORICAL_CHAIN = '''
def run_bench():
    while True:
        st, n = msg[0], msg[1]
        if mk1_ghost is not None and down is not None:
            if mk1_ghost.consume(n, down, now=now_mono):
                continue
        if down is not None and is_stop_all(apc_label, n):
            routing_shift = stop_all_took_shift
        scene_row = scene_press_row(n, scene_notes=scene_launch_notes)
        if scene_row is not None:
            continue
        if slot_surface is not None and down is not None and slot_surface.handles(n):
            continue
        if down is not None and is_reserved_grid_note(n):
            continue
        if down is not None and n == shift_note:
            shift_held = down
        if down and handle_arrow(n):
            continue
        if down is not None and n in (shift_note, stop_all_note):
            continue
        if down is not None and n in by_note:
            by_note[n].on_pad_down()
'''


class RoutingIsNotAnIfChainTests(unittest.TestCase):
    """The one guard. If routing moves back into the loop, this names the line.

    Deliberately NOT a substring check. `assertIn("bindings.note_event", src)`
    would pass over a re-grown chain sitting right beside the call, and would
    also be satisfied by a comment — which is how a guard on this branch passed
    on 2026-08-30 while the lifecycle it guarded had been deleted.
    """

    def test_the_note_never_escapes_the_router(self) -> None:
        loop = _event_loop(BENCH.read_text(encoding="utf-8"))
        allowed, escaped = _note_uses(loop)
        self.assertEqual(
            [], [f"{BENCH}:{node.lineno}" for node in escaped],
            "the incoming note is read outside a call on `bindings`. That is a "
            "routing decision the table cannot see, and the last time one "
            "existed four buttons on the panel did nothing for six weeks.",
        )
        # Positive control against the guard passing because it found nothing:
        # the loop still HAS a note, and still hands it to the router.
        self.assertGreaterEqual(
            len(allowed), 2,
            "no note reaches the router at all — either the loop stopped "
            "routing or this guard is looking at the wrong function",
        )

    def test_the_guard_catches_the_chain_it_replaced(self) -> None:
        """Proof the guard is not vacuous, on the real historical source.

        Every one of these lines shipped. If the detector cannot see them it
        cannot see the next one either.
        """
        loop = _event_loop(HISTORICAL_CHAIN)
        _allowed, escaped = _note_uses(loop)
        lines = sorted({node.lineno for node in escaped})
        self.assertGreaterEqual(
            len(lines), 8,
            f"the guard found only {lines} in a chain with nine note branches",
        )

    def test_the_guard_catches_a_note_laundered_through_a_local(self) -> None:
        """The arrow bug's exact shape: the `if` never mentions the note.

        `if scene_row is not None:` is not a test on `n` — the decision was made
        one line earlier. A guard that only looked at `if` conditions would have
        passed the code that killed banking.
        """
        sample = '''
def run_bench():
    while True:
        st, n = msg[0], msg[1]
        bindings.note_event(n, down=down, now=now)
        scene_row = scene_press_row(n, scene_notes=scene_launch_notes)
        if scene_row is not None:
            continue
'''
        _allowed, escaped = _note_uses(sample_loop := _event_loop(sample))
        self.assertEqual(len(escaped), 1)
        self.assertEqual(escaped[0].lineno, 6)
        self.assertIsInstance(sample_loop, ast.While)

    def test_prose_cannot_satisfy_the_guard(self) -> None:
        """A comment naming the router is not the router.

        The lesson of 2026-08-30: a substring guard passed while the guarded
        code was deleted, because a comment mentioned the name. This asserts the
        detector reads AST nodes — a chain smothered in reassuring comments is
        still a chain.
        """
        sample = '''
def run_bench():
    while True:
        # Routing goes through bindings.note_event(n, down=down) — see
        # binding_table. Nothing below decides anything.
        st, n = msg[0], msg[1]
        if n in by_note:
            pass
'''
        _allowed, escaped = _note_uses(_event_loop(sample))
        self.assertEqual(len(escaped), 1, "a comment satisfied the guard")

    def test_the_loop_finder_refuses_to_find_nothing(self) -> None:
        with self.assertRaises(AssertionError):
            _event_loop("def run_bench():\n    return 0\n")


class BindingCollisionTests(unittest.TestCase):
    """Two rows may not answer one event. The table refuses at import."""

    def test_the_live_table_has_no_collisions(self) -> None:
        self.assertEqual({}, bt.binding_collisions(bt.BINDINGS))

    def test_a_duplicate_row_is_refused_and_names_both_lines(self) -> None:
        twin = bt.Binding(
            control="stop_all_clips", gesture=bt.PRESS, layer=bt.BASE,
            mode=bt.ANY_MODE, actions=("noop",), defined_at=4242,
        )
        with self.assertRaises(ValueError) as caught:
            bt.assert_no_binding_collisions(bt.BINDINGS + (twin,))
        message = str(caught.exception)
        self.assertIn("stop_all_clips", message)
        self.assertIn(":4242", message, "the report must name the source line")

    def test_a_shift_row_cannot_hide_under_an_any_layer_row(self) -> None:
        """The shape a first-match-wins lookup would have swallowed.

        `scene_launch_1` is bound on ANY layer. Adding a SHIFT-only row for it
        is not a refinement, it is two answers to one press — and a lookup that
        returned the first hit would simply have shadowed one of them, which is
        the mk2 arrow bug with different nouns.
        """
        shadowed = bt.Binding(
            control="scene_launch_1", gesture=bt.PRESS, layer=bt.SHIFT,
            mode=bt.ANY_MODE, actions=("noop",), defined_at=99,
        )
        clash = bt.binding_collisions(bt.BINDINGS + (shadowed,))
        self.assertIn(("scene_launch_1", bt.MULTIGRID, bt.SHIFT, bt.PRESS), clash)
        self.assertNotIn(("scene_launch_1", bt.MULTIGRID, bt.BASE, bt.PRESS), clash)

    def test_a_row_naming_a_control_the_registry_does_not_have_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            bt.Binding(control="scene_launch_9", gesture=bt.PRESS,
                       layer=bt.BASE, mode=bt.ANY_MODE, actions=("noop",))

    def test_a_row_with_an_unknown_action_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            bt.Binding(control="shift", gesture=bt.PRESS, layer=bt.BASE,
                       mode=bt.ANY_MODE, actions=("do_the_thing",))


class ReachabilityTests(unittest.TestCase):
    """A binding nobody can trigger, found without a device."""

    def test_the_only_unreachable_rows_on_the_attached_mk2_are_the_arrows(self) -> None:
        """The banking bug, as a standing assertion rather than an anecdote.

        Eight rows: four arrows, press and release. They are unreachable
        because `control_registry` records the mk2 arrow notes as UNKNOWN —
        the recalled 0x70-0x73 were measured to be scene buttons. This is not
        an error; it is the truth about the device, and the session banner says
        so. It becomes an error the moment it is a SURPRISE, which is what this
        pins: any NEW unreachable binding fails here, by name and line.
        """
        found = bt.unreachable("mk2")
        expected = sorted(
            (f"bank_{direction}", gesture)
            for direction in ("up", "down", "left", "right")
            for gesture in (bt.PRESS, bt.RELEASE)
        )
        self.assertEqual(
            sorted((u.binding.control, u.binding.gesture) for u in found),
            expected,
            # The message carries every finding with its source line, so a new
            # unreachable binding is reported as a place to go and read, not as
            # a count that moved.
            "the set of unreachable bindings changed:\n  "
            + "\n  ".join(str(u) for u in found),
        )
        for entry in found:
            with self.subTest(control=entry.binding.control):
                self.assertIn("binding_table.py:", str(entry))
                self.assertIn("no established note", str(entry))

    def test_nothing_is_unreachable_on_the_mk1(self) -> None:
        self.assertEqual((), bt.unreachable("mk1"))

    def test_the_reported_line_is_the_line_the_row_is_written_on(self) -> None:
        """A finding that names a line the reader cannot find is not a finding."""
        source = TABLE.read_text(encoding="utf-8").splitlines()
        for entry in bt.unreachable("mk2"):
            with self.subTest(line=entry.binding.defined_at):
                text = source[entry.binding.defined_at - 1]
                self.assertIn("_row(", text)

    def test_every_control_has_a_row_for_every_gesture_it_can_send(self) -> None:
        """An unbound control and a forgotten one look identical on the device.

        All eight track-select buttons are unbound: the note falls through the
        whole surface and is not even logged, so a wrong note number and a
        button nobody touched produce the same silence. They are `noop` rows
        here so that the silence is a statement.
        """
        self.assertEqual({}, bt.missing_rows())

    def test_the_completeness_check_catches_a_dropped_row(self) -> None:
        kept = [b for b in bt.BINDINGS if not (
            b.control == "track_select_3" and b.gesture == bt.RELEASE
        )]
        self.assertEqual(len(kept), len(bt.BINDINGS) - 1)
        original = bt.BINDINGS
        try:
            bt.BINDINGS = tuple(kept)
            gaps = bt.missing_rows()
        finally:
            bt.BINDINGS = original
        self.assertIn(("track_select_3", bt.MULTIGRID, bt.BASE), gaps)
        self.assertEqual(gaps[("track_select_3", bt.MULTIGRID, bt.BASE)], (bt.RELEASE,))


class OwnershipTests(unittest.TestCase):
    """One control, one owner — and where there are two, the table says so."""

    def test_every_action_names_a_module_the_registry_knows(self) -> None:
        for action in bt.ACTIONS.values():
            with self.subTest(action=action.name):
                self.assertTrue(
                    action.owner == reg.UNOWNED or action.owner in reg.OWNERS
                )

    def test_the_only_control_with_two_owners_is_shift(self) -> None:
        """Spec defect D4, enumerated rather than described.

        Shift latches the event loop's modifier state AND feeds
        `apc_transport`'s combo, from one press, in that order. Every other
        control reaches exactly one module. `control_registry` records `shift`
        as contested for the same reason.
        """
        two = {
            b.control for b in bt.BINDINGS
            if len({o for o in b.owners if o != reg.UNOWNED}) > 1
        }
        self.assertEqual(two, {"shift"})
        self.assertIn("apc_transport", reg.control("shift").contested)

    def test_the_shift_latch_lands_before_the_second_owner_sees_the_press(self) -> None:
        """Order between two owners is in the row, not in statement order.

        The old loop set the latch at branch 4 and deliberately did not
        `continue`, so the same event fell through to the transport combo at
        branch 6. Forty-five lines and three `continue`s apart.
        """
        row = next(b for b in bt.BINDINGS
                   if b.control == "shift" and b.gesture == bt.PRESS)
        self.assertEqual(row.actions, ("latch_shift", "transport_note"))

    def test_a_hold_row_says_who_counts_the_milliseconds(self) -> None:
        for row in bt.BINDINGS:
            if row.gesture != bt.HOLD:
                continue
            with self.subTest(control=row.control):
                self.assertIsNotNone(row.timing_owner)
                self.assertIsNotNone(row.hold_env)
                self.assertNotEqual(row.fired_by, bt.BY_ROUTER)

    def test_a_timed_row_claiming_the_router_fires_it_is_refused(self) -> None:
        """No MIDI message says "held for three seconds"."""
        with self.assertRaises(ValueError):
            bt.Binding(control="stop_all_clips", gesture=bt.HOLD, layer=bt.SHIFT,
                       mode=bt.ANY_MODE, actions=("clear_all_loops",),
                       fired_by=bt.BY_ROUTER, hold_env="MPE_APC_HOLD_MS",
                       timing_owner="apc_transport")


class HoldThresholdsAreWiredTests(unittest.TestCase):
    """A HOLD row's number must be the number that runs.

    Without this the table is a place to write a threshold that nothing reads —
    prose with a dataclass around it. The check is structural: the bench must
    read the row's env var into a name and hand that name to a constructor of
    the row's declared timing owner.
    """

    #: Which call constructs each timing owner in the bench.
    CONSTRUCTORS = {
        "apc_transport": ("ShiftHoldCombo",),
        "slot_surface": ("SlotSurface",),
        "track_gesture": ("build_track_gestures",),
    }

    def setUp(self) -> None:
        self.tree = ast.parse(BENCH.read_text(encoding="utf-8"))

    def _name_holding_env(self, var: str) -> str | None:
        """The local the bench assigns `os.environ.get(var, ...)` to."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            if not isinstance(node.targets[0], ast.Name):
                continue
            for call in ast.walk(node.value):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == var
                ):
                    return node.targets[0].id
        return None

    def _reaches(self, local: str, constructors: tuple[str, ...]) -> bool:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in constructors:
                continue
            for keyword in node.keywords:
                for used in ast.walk(keyword.value):
                    if isinstance(used, ast.Name) and used.id == local:
                        return True
        return False

    def test_every_hold_row_threshold_reaches_its_timing_owner(self) -> None:
        seen = 0
        for row in bt.BINDINGS:
            if row.gesture != bt.HOLD:
                continue
            seen += 1
            with self.subTest(control=row.control, owner=row.timing_owner):
                local = self._name_holding_env(row.hold_env)
                self.assertIsNotNone(
                    local, f"the bench never reads {row.hold_env}"
                )
                self.assertTrue(
                    self._reaches(local, self.CONSTRUCTORS[row.timing_owner]),
                    f"{row.hold_env} is read but never reaches "
                    f"{self.CONSTRUCTORS[row.timing_owner]} — the row states a "
                    "threshold nothing counts",
                )
        self.assertGreater(seen, 0, "no HOLD rows: this test checked nothing")

    def test_the_wiring_check_fails_on_an_env_var_nothing_reads(self) -> None:
        self.assertIsNone(self._name_holding_env("MPE_APC_NO_SUCH_HOLD_MS"))


class RouterTests(unittest.TestCase):
    """The two latches the routing decision needs, and nothing else."""

    def _router(self, variant: str = "mk2", multigrid: bool = True):
        fired: list[tuple[str, int, bool, str]] = []

        def record(name):
            def handler(number, down, control):
                fired.append((name, number, down, control))
                if name == "latch_shift":
                    router.set_shift(down)
            return handler

        router = bt.BindingRouter(
            bt.for_surface(variant, multigrid=multigrid),
            actions={name: record(name) for name in bt.ACTIONS},
        )
        return router, fired

    def test_the_bottom_button_keeps_the_hat_it_took_at_press_down(self) -> None:
        """Release Shift first and the combo must not be left holding a button.

        If the layer were re-read on the up edge, the down would go to
        `apc_transport` and the up to `slot_surface` — and `ShiftHoldCombo`
        would sit there with `_target_down` true forever.
        """
        router, fired = self._router()
        shift = router._table.note("shift")
        stop = router._table.note("stop_all_clips")
        router.note_event(shift, down=True, now=0.0)
        router.note_event(stop, down=True, now=0.1)
        router.note_event(shift, down=False, now=0.2)   # Shift released FIRST
        router.note_event(stop, down=False, now=0.3)
        stop_events = [f for f in fired if f[1] == stop]
        self.assertEqual(
            [(f[0], f[2]) for f in stop_events],
            [("transport_note", True), ("transport_note", False)],
        )

    def test_the_bottom_button_alone_is_row_zero(self) -> None:
        router, fired = self._router()
        stop = router._table.note("stop_all_clips")
        router.note_event(stop, down=True, now=0.0)
        self.assertEqual([f[0] for f in fired], ["scene_launch"])
        self.assertEqual(bt.scene_row("stop_all_clips"), 0)

    def test_shift_latches_before_apc_transport_sees_it(self) -> None:
        router, fired = self._router()
        shift = router._table.note("shift")
        router.note_event(shift, down=True, now=0.0)
        self.assertEqual([f[0] for f in fired], ["latch_shift", "transport_note"])
        self.assertTrue(router.shift_held)
        router.note_event(shift, down=False, now=0.1)
        self.assertFalse(router.shift_held)

    def test_a_non_note_message_routes_to_nothing(self) -> None:
        router, fired = self._router()
        self.assertIsNone(router.note_event(0x00, down=None, now=0.0))
        self.assertEqual([], fired)

    def test_the_bench_wires_every_action_in_the_table(self) -> None:
        """A missing handler is a crash at session start, not a quiet gap.

        `BindingRouter.__init__` raises, which is right — but it raises on the
        appliance, at boot, after a deploy. Nothing in the suite executes
        `run_bench` (it needs rtmidi and a device), so this reads the bench's
        handler dict out of its AST instead. Adding a row with a new action and
        forgetting to wire it fails here, on the laptop, in a second.
        """
        tree = ast.parse(BENCH.read_text(encoding="utf-8"))
        built = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BindingRouter"
        )
        mapping = next(k.value for k in built.keywords if k.arg == "actions")
        self.assertIsInstance(mapping, ast.Dict)
        wired = {k.value for k in mapping.keys if isinstance(k, ast.Constant)}
        # Not every action: `slot_delete` and `clip_clear` are BY_OWNER —
        # SlotSurface and TrackGesture run their own clocks and their own
        # consequence, and the router is not in that path. Wiring them here
        # would be a handler that never runs, which reads as coverage.
        dispatchable = {
            name for b in bt.BINDINGS
            if b.fired_by in (bt.BY_ROUTER, bt.BY_BENCH_POLL)
            for name in b.actions
        }
        self.assertEqual(wired, dispatchable)
        self.assertEqual(
            {name for b in bt.BINDINGS if b.fired_by == bt.BY_OWNER
             for name in b.actions},
            set(bt.ACTIONS) - dispatchable,
        )
        handlers = {v.id for v in mapping.values if isinstance(v, ast.Name)}
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("act_")
        }
        self.assertEqual(
            defined - handlers, set(),
            "an act_* closure the router never receives is dead code that "
            "reads as a wired control",
        )

    def test_the_router_refuses_to_start_with_an_unwired_action(self) -> None:
        """A row nobody implemented is a control that silently does nothing."""
        with self.assertRaises(ValueError) as caught:
            bt.BindingRouter(bt.for_surface("mk2", multigrid=True), actions={})
        self.assertIn("no handler for", str(caught.exception))

    def test_a_hold_row_cannot_be_fired_by_the_wrong_poller(self) -> None:
        router, _fired = self._router()
        with self.assertRaises(ValueError):
            router.fire("grid_r0_c0", bt.HOLD, layer=bt.BASE)

    def test_the_combo_rows_fire_from_the_bench_poller(self) -> None:
        router, fired = self._router()
        router.fire("stop_all_clips", bt.HOLD)
        router.fire("stop_all_clips", bt.TAP)
        self.assertEqual([f[0] for f in fired],
                         ["clear_all_loops", "stop_all_loops"])


def _old_chain_branch(
    note: int,
    down: bool | None,
    state: dict,
    *,
    label: str,
    multigrid: bool,
    view,
    arrows: dict,
    by_note: dict,
    scene_notes: tuple,
    shift_note: int,
    stop_all_note: int,
) -> tuple[str, ...]:
    """The pre-stage-5 event loop, as a pure function. Reference, not code.

    Transcribed from `sooperlooper-apc-bench.py` at `57f6dcd`, branch for
    branch and in source order, returning which branch took the event. It
    exists so the table can be shown to route exactly the way the `if`-chain
    did — over the whole note space, both variants, both modes, both edges,
    Shift up and down. "It looks equivalent" is what the arrow tuple had going
    for it.
    """
    from scripts.sooperlooper.apc_grid import is_clip_note, is_reserved_grid_note
    from scripts.sooperlooper.apc_panel import is_stop_all, scene_press_row

    # Every branch label carries the EDGE that took it. Without this the
    # differential is blind to the defect it most needs to see: swap
    # `slot_press` and `slot_release` in the binding table and both models
    # still say "slot", so 1536 subtests stay green while every pad on the
    # surface fires on the wrong edge. A press that acts on release is not a
    # subtle regression — it is the instrument feeling broken in the hand.
    edge = "down" if down else "up"

    def tag(*names):
        return tuple(f"{n}:{edge}" for n in names)

    out: list[str] = []
    if down is None:
        return ()
    if is_stop_all(label, note):
        if down:
            state["stop_all_took_shift"] = state["shift_held"]
        routing_shift = state["stop_all_took_shift"]
    else:
        routing_shift = state["shift_held"]
    scene_row = scene_press_row(
        note, scene_notes=scene_notes, apc_label=label, shift_held=routing_shift
    )
    if scene_row is not None:
        return tag("scene")
    if multigrid and view.cell_for_note(note) is not None:
        return tag("slot")
    if is_reserved_grid_note(note):
        return tag("reserved")
    if note == shift_note:
        state["shift_held"] = down
        out.append(f"latch:{edge}")
    if down and note in arrows:
        return tuple(out) + tag("arrow")
    if note in (shift_note, stop_all_note):
        return tuple(out) + tag("transport")
    if note in by_note:
        return tuple(out) + tag("clip")
    if is_clip_note(note):
        return tuple(out) + tag("clip_ignored")
    return tuple(out)


#: One action -> the branch label the old chain would have used for it. `noop`
#: maps to nothing, because the old chain had no branch: the note fell through
#: the whole loop.
_ACTION_TO_BRANCH = {
    "scene_launch": ("scene", "down"),
    "scene_release_consumed": ("scene", "up"),
    "slot_press": ("slot", "down"),
    "slot_release": ("slot", "up"),
    "clip_press": ("clip", "down"),
    "clip_release": ("clip", "up"),
    "ignore_reserved_row": ("reserved", None),
    "latch_shift": ("latch", None),
    "transport_note": ("transport", None),
    "bank_scroll": ("arrow", None),
    "noop": None,
}


def _fired_label(name: str, down: bool) -> str | None:
    """The label the table side records for one action on one edge.

    The edge is pinned for every action whose NAME encodes one, and that
    pinning is the whole point. Tag the branch with the edge of the *event*
    instead and a table that fires `slot_release` on a press still reports
    `slot:down` — which is how 1536 subtests stayed green through a swap of
    every pad's edges. The reference model reports the edge the event arrived
    on; this side reports the edge the action MEANS. They agree only when the
    action is bound to the edge it is named for.

    A pinned edge of `None` means "fires on whichever edge it is bound to" —
    the latch, the transport notes and the reserved rows genuinely act on both
    and the old chain did the same, so there the event's edge is the honest
    label.
    """
    mapped = _ACTION_TO_BRANCH[name]
    if mapped is None:
        return None
    branch, pinned = mapped
    return f"{branch}:{pinned or ('down' if down else 'up')}"


class BehaviourIsUnchangedTests(unittest.TestCase):
    """The table routes every note exactly where the `if`-chain did.

    This is the evidence for "no behaviour change", and it is a differential
    rather than a claim: 128 notes x both edges x Shift up and down x both
    variants x both modes, against a transcription of the loop as it stood
    before the stage.
    """

    def _sweep(
        self, variant: str, multigrid: bool, shift_first: bool, strict: bool = False
    ) -> None:
        """`strict` stops the first mismatch escaping as an exception.

        `subTest` swallows the failure it records, which is right for a sweep
        that should report every divergence — and useless for the negative
        control below, which needs the sweep to actually raise.
        """
        import contextlib
        from scripts.sooperlooper.apc_grid import NUM_LOOPS, GridView
        from scripts.sooperlooper.apc_transport import (
            resolve_apc_transport_notes,
            resolve_arrow_notes,
            resolve_scene_launch_notes,
        )
        from scripts.sooperlooper.track_gesture import notes_for_view

        port = "APC mini mk2 MIDI 1" if variant == "mk2" else "APC MINI"
        shift_note, stop_all_note, label = resolve_apc_transport_notes(port)
        scene_notes = resolve_scene_launch_notes(label)
        arrows = resolve_arrow_notes(port)
        view = GridView(num_loops=NUM_LOOPS)

        class _Fake:
            def __init__(self, loop):
                self.loop = loop
        by_note = notes_for_view([_Fake(i) for i in range(NUM_LOOPS)], view)

        table = bt.for_surface(label, multigrid=multigrid)
        fired: list[str] = []
        divergences: list[tuple] = []
        compared = 0

        def make(name):
            def handler(_number, down, _control):
                label_for = _fired_label(name, down)
                if label_for is not None:
                    fired.append(label_for)
                if name == "latch_shift":
                    router.set_shift(down)
            return handler

        for down in (True, False):
            for note in range(128):
                # Both models start from the same state and are driven through
                # the same events, so the latch is under test too rather than
                # being supplied to the table by hand.
                router = bt.BindingRouter(
                    table, actions={n: make(n) for n in bt.ACTIONS}
                )
                state = {"shift_held": False, "stop_all_took_shift": False}
                if shift_first:
                    _old_chain_branch(
                        shift_note, True, state, label=label, multigrid=multigrid,
                        view=view, arrows=arrows, by_note=by_note,
                        scene_notes=scene_notes, shift_note=shift_note,
                        stop_all_note=stop_all_note,
                    )
                    router.note_event(shift_note, down=True, now=0.0)
                fired.clear()
                old = _old_chain_branch(
                    note, down, state, label=label, multigrid=multigrid,
                    view=view, arrows=arrows, by_note=by_note,
                    scene_notes=scene_notes, shift_note=shift_note,
                    stop_all_note=stop_all_note,
                )
                router.note_event(note, down=down, now=1.0)
                # Compare every note on every edge, and open a subTest only
                # for a DIVERGENCE.
                #
                # This used to wrap all 1536 comparisons in `subTest`, which
                # reported 1536 passing subtests per call and was most of the
                # 2451 in the suite. The audit's pruning plan proposed
                # collapsing it to one 1536-entry `assertEqual`; that keeps the
                # detection power and throws away the localization, because
                # `maxDiff` truncates a dict that size to "Diff is N characters
                # long" — a red test naming no pad and no edge, on the sweep
                # whose whole job is to say WHICH pad moved.
                #
                # Comparing in plain code and reporting only what differs keeps
                # both: identical comparisons, failures that still name the pad
                # and the edge, and nothing reported when there is nothing to
                # say.
                if strict:
                    self.assertEqual(tuple(fired), old)
                    self.assertNotIn("clip_ignored:down", old)
                    self.assertEqual(state["shift_held"], router.shift_held)
                elif (tuple(fired) != old
                      or "clip_ignored:down" in old
                      or state["shift_held"] != router.shift_held):
                    divergences.append(
                        (hex(note), down, tuple(fired), old,
                         state["shift_held"], router.shift_held)
                    )
                compared += 1

        if strict:
            return
        # A sweep that compared nothing would satisfy every assertion above.
        self.assertEqual(compared, 256, "the sweep did not cover 128 notes x 2 edges")
        if divergences:
            shown = "\n".join(
                f"  note {n} down={d}: table={f!r} chain={o!r} "
                f"shift chain={sc} router={sr}"
                for n, d, f, o, sc, sr in divergences[:8]
            )
            more = ("\n  ... and %d more" % (len(divergences) - 8)
                    if len(divergences) > 8 else "")
            self.fail(
                f"{len(divergences)} of {compared} routings diverged from the "
                f"pre-stage-5 chain "
                f"(variant={variant}, multigrid={multigrid}, "
                f"shift_first={shift_first}):\n{shown}{more}"
            )

    def test_mk2_multigrid_matches(self) -> None:
        self._sweep("mk2", multigrid=True, shift_first=False)
        self._sweep("mk2", multigrid=True, shift_first=True)

    def test_mk2_single_clip_matches(self) -> None:
        self._sweep("mk2", multigrid=False, shift_first=False)
        self._sweep("mk2", multigrid=False, shift_first=True)

    def test_mk1_matches(self) -> None:
        self._sweep("mk1", multigrid=True, shift_first=False)
        self._sweep("mk1", multigrid=False, shift_first=True)

    def test_every_ordering_of_the_transport_chord_matches(self) -> None:
        """The per-note sweep cannot see a latch. This drives sequences.

        Both latches are order-dependent by nature — "did the bottom button take
        Shift when it went down" and "is Shift down" — so a model that agrees on
        every note in isolation can still disagree on `Shift down, StopAll down,
        Shift UP, StopAll up`, which is the ordering a player produces by
        letting go of Shift first. Four buttons, both edges, every sequence of
        four events: 4096 sequences, 16384 compared events, and the latch itself
        compared after each one.
        """
        import itertools

        from scripts.sooperlooper.apc_grid import NUM_LOOPS, GridView
        from scripts.sooperlooper.apc_transport import (
            resolve_apc_transport_notes,
            resolve_arrow_notes,
            resolve_scene_launch_notes,
        )
        from scripts.sooperlooper.track_gesture import notes_for_view

        port = "APC mini mk2 MIDI 1"
        shift_note, stop_all_note, label = resolve_apc_transport_notes(port)
        scene_notes = resolve_scene_launch_notes(label)
        arrows = resolve_arrow_notes(port)
        view = GridView(num_loops=NUM_LOOPS)

        class _Fake:
            def __init__(self, loop):
                self.loop = loop
        by_note = notes_for_view([_Fake(i) for i in range(NUM_LOOPS)], view)

        table = bt.for_surface(label, multigrid=True)
        fired: list[str] = []

        def make(name):
            def handler(_number, down, _control):
                mapped = _fired_label(name, down)
                if mapped is not None:
                    fired.append(mapped)
                if name == "latch_shift":
                    router.set_shift(down)
            return handler

        scene = scene_notes[2]
        pad = 0x05
        events = [
            (note, down)
            for note in (shift_note, stop_all_note, scene, pad)
            for down in (True, False)
        ]
        compared = 0
        for sequence in itertools.product(events, repeat=4):
            router = bt.BindingRouter(table, actions={n: make(n) for n in bt.ACTIONS})
            state = {"shift_held": False, "stop_all_took_shift": False}
            for note, down in sequence:
                fired.clear()
                old = _old_chain_branch(
                    note, down, state, label=label, multigrid=True, view=view,
                    arrows=arrows, by_note=by_note, scene_notes=scene_notes,
                    shift_note=shift_note, stop_all_note=stop_all_note,
                )
                router.note_event(note, down=down, now=1.0)
                compared += 1
                self.assertEqual(tuple(fired), old, f"diverged at {sequence}")
                self.assertEqual(
                    state["shift_held"], router.shift_held,
                    f"the modifier latch diverged at {sequence}",
                )
        # Positive control on the loop itself: a sweep that compared nothing
        # would pass every assertion above.
        self.assertEqual(compared, len(events) ** 4 * 4)
        self.assertEqual(compared, 16384)

    def test_the_sweep_notices_a_routing_change(self) -> None:
        """A differential that cannot come out unequal proves nothing.

        Re-point one action's label and the sweep must fail — otherwise the
        three tests above are asserting that two things it never compared are
        the same.
        """
        original = _ACTION_TO_BRANCH["slot_press"]
        try:
            _ACTION_TO_BRANCH["slot_press"] = ("transport", "down")
            with self.assertRaises(AssertionError):
                self._sweep("mk2", multigrid=True, shift_first=False, strict=True)
        finally:
            _ACTION_TO_BRANCH["slot_press"] = original
        self._sweep("mk2", multigrid=True, shift_first=False, strict=True)

    def test_the_differential_can_fail(self) -> None:
        """A comparison that cannot come out unequal proves nothing.

        Feed the reference the notes the mk2 arrows used to claim and it says
        `scene` — which is the swallowing that killed banking. The table says
        the same thing today, because those notes ARE scene buttons; the
        difference is that the table can no longer be given a second claim on
        them.
        """
        from scripts.sooperlooper.apc_grid import NUM_LOOPS, GridView
        from scripts.sooperlooper.apc_transport import (
            resolve_apc_transport_notes,
            resolve_scene_launch_notes,
        )

        _s, stop_all_note, label = resolve_apc_transport_notes("APC mini mk2 MIDI 1")
        scene_notes = resolve_scene_launch_notes(label)
        for note in (0x70, 0x71, 0x72, 0x73):
            branch = _old_chain_branch(
                note, True, {"shift_held": False, "stop_all_took_shift": False},
                label=label, multigrid=True, view=GridView(num_loops=NUM_LOOPS),
                arrows={note: "up"}, by_note={}, scene_notes=scene_notes,
                shift_note=0x7A, stop_all_note=stop_all_note,
            )
            self.assertEqual(branch, ("scene:down",))
            self.assertNotEqual(branch, ("arrow:down",))


if __name__ == "__main__":
    unittest.main()
