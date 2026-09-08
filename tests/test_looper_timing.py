"""`looper_timing` — the one place that decides when an action takes effect.

Two kinds of test here, and the second is the one that matters.

The first kind checks the answers: given what the instrument is doing, does
the module say the right thing? Those are ordinary.

The second kind checks that **nothing else answers the question**. That is the
whole point of the module, and it is the property the twenty-three specs and a
1,113-line decision log could never hold: they described the behaviour, and
nothing stopped a sixth place from quietly deciding it differently.
"""

from tests import conftest  # noqa: F401 — bare sooperlooper imports

import ast
import inspect
import pathlib
import unittest

# Bare, exactly as every production module imports it. Package-qualified
# (`scripts.sooperlooper.looper_timing`) loads a SECOND module object with
# its own RULES dict, so these assertions would guard a copy nothing runs.
import looper_timing as timing
import slot_runtime
import loop_model
import track_gesture
from sl_loop_states import SL_STATE_OFF


def _actions_asked_in(path: pathlib.Path) -> set[str]:
    """Which timing actions this file actually ASKS about.

    An AST walk for `timing.when(timing.X, ...)`, returning the `X` names. A
    substring search cannot tell one call site from another, which is how a
    guard on the whole module stayed green while one branch inside it decided
    for itself.
    """
    asked: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "when"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Attribute):
            asked.add(first.attr)
        elif isinstance(first, ast.IfExp):
            for branch in (first.body, first.orelse):
                if isinstance(branch, ast.Attribute):
                    asked.add(branch.attr)
    return asked


def sounding_grid(**kw) -> timing.Session:
    """A grid is up and the pressed track is playing. The ordinary case."""
    base = dict(grid=True, sounding=True, track_sounding=True)
    base.update(kw)
    return timing.Session(**base)


class WhenTests(unittest.TestCase):
    """The answers."""

    def test_on_a_grid_with_music_playing_everything_musical_waits(self) -> None:
        for action in (timing.RECORD_START, timing.RECORD_CLOSE,
                       timing.CLIP_STOP, timing.CLIP_LAUNCH,
                       timing.SLOT_SWITCH):
            with self.subTest(action=action):
                self.assertFalse(timing.when(action, sounding_grid()).immediate)

    def test_transport_and_destructive_actions_never_wait(self) -> None:
        for action in (timing.STOP_ALL, timing.CLEAR_ALL, timing.CLIP_CLEAR,
                       timing.FADER_MOVE, timing.OVERDUB):
            with self.subTest(action=action):
                self.assertTrue(timing.when(action, sounding_grid()).immediate)

    def test_the_defining_take_is_always_free(self) -> None:
        """No bar exists yet, and quantizing it would snap it to a stale one."""
        for action in timing.ACTIONS:
            with self.subTest(action=action):
                self.assertTrue(
                    timing.when(action, sounding_grid(defining=True)).immediate)

    def test_with_no_grid_nothing_waits(self) -> None:
        quiet = timing.Session(grid=False, sounding=False)
        for action in timing.ACTIONS:
            with self.subTest(action=action):
                self.assertTrue(timing.when(action, quiet).immediate)

    def test_launching_into_silence_is_immediate(self) -> None:
        """The law. You cannot be late for something that has not started."""
        m = timing.when(timing.CLIP_LAUNCH,
                        timing.Session(grid=True, sounding=False))
        self.assertTrue(m.immediate)
        self.assertIn("sounding", m.why)

    def test_a_wait_with_nothing_playing_falls_back_to_the_grid_timer(self) -> None:
        """No wrap to ride, so our own timer against the grid's phase."""
        m = timing.when(timing.CLIP_STOP,
                        timing.Session(grid=True, sounding=True,
                                       track_sounding=False))
        self.assertEqual(m.lands_on, timing.GRID_BOUNDARY)

    def test_an_unknown_action_is_refused_not_guessed(self) -> None:
        with self.assertRaises(ValueError):
            timing.when("wiggle the thing", sounding_grid())

    def test_every_wait_can_say_why_it_is_waiting(self) -> None:
        """A deferred action has always looked exactly like a dropped press."""
        for action in timing.ACTIONS:
            with self.subTest(action=action):
                self.assertTrue(str(timing.when(action, sounding_grid())).strip())


class InvariantTests(unittest.TestCase):
    """The rules the table holds itself to, at import."""

    def test_every_action_has_a_rule(self) -> None:
        self.assertEqual(set(timing.RULES), set(timing.ACTIONS))

    def test_the_engine_may_not_be_given_a_gate_it_cannot_see(self) -> None:
        """SooperLooper is told about the grid. It is never told what is
        sounding. A rule claiming otherwise would be believed."""
        with self.assertRaises(ValueError):
            timing.Rule(gate=timing.WHILE_SOUNDING,
                        enforced_by=timing.BY_ENGINE, why="cannot work")
        for action, rule in timing.RULES.items():
            with self.subTest(action=action):
                if rule.enforced_by == timing.BY_ENGINE:
                    self.assertNotEqual(rule.gate, timing.WHILE_SOUNDING)

    def test_a_rule_must_say_why(self) -> None:
        with self.assertRaises(ValueError):
            timing.Rule(gate=timing.NEVER, why="   ")

    def test_engine_controls_track_the_grid_and_never_round(self) -> None:
        on = timing.engine_controls(grid=True)
        off = timing.engine_controls(grid=False)
        self.assertEqual(on["quantize"], 1.0)
        self.assertEqual(on["mute_quantized"], 1.0)
        self.assertEqual(set(off.values()), {0.0})
        for controls in (on, off):
            self.assertEqual(controls["round"], 0.0,
                             "round on top of a quantized stop adds a cycle")


class OneAuthorityTests(unittest.TestCase):
    """Nothing else decides. This is the property, not the answers."""

    #: Files allowed to write a quantize control value straight out, and why.
    #: Anything not listed here has to go through `engine_controls`. Empty on
    #: purpose: the one entry that lived here named a bench spike, and deleting
    #: the spike was cheaper than carrying an exception for it.
    LITERAL_ALLOWED: dict[str, str] = {
        "measure-loop-alignment.py":
            "an instrument, and driving `quantize` IS its measurement. It "
            "reads the old value into `restore` first and puts it back, which "
            "is the property that makes it safe -- not that it is a script.",
    }
    #: DERIVED from the authority, not typed here. Written by hand it covered
    #: `quantize` and `mute_quantized` and silently missed `sync` and `round`,
    #: which are equally part of what quantization IS to the engine.
    CONTROLS = set(timing.engine_controls(grid=True))

    def _sources(self):
        """Every looper source, including the ones a level up.

        The glob was `scripts/sooperlooper/*.py` only, so
        `scripts/measure-loop-alignment.py` set `quantize` and `sync` directly,
        outside the guard, for as long as it has existed.
        """
        root = pathlib.Path(track_gesture.__file__).parent
        for path in sorted(root.glob("*.py")):
            yield path
        for name in ("sooperlooper-apc-bench.py", "looper-session.py",
                     "measure-loop-alignment.py"):
            extra = root.parent / name
            if extra.exists():
                yield extra

    def test_no_module_writes_a_quantize_value_of_its_own(self) -> None:
        """`["mute_quantized", 1.0]` written anywhere is a sixth opinion.

        That is not hypothetical. `settle_stop_all` and `looper_songs` each
        restored `mute_quantized` to 1.0 unconditionally while
        `set_grid_active` set it from the grid, so after a Stop All in a
        gridless session a per-clip stop waited for a boundary no tempo
        defined. Two files, both plausible on their own, and no reader could
        see it. Now the value has one source and this refuses a second.
        """
        offences = []
        for path in self._sources():
            if path.name in self.LITERAL_ALLOWED or path.name == "looper_timing.py":
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                # Tuple as well as List: `("quantize", 1.0)` is the same
                # opinion written with different brackets, and the guard did
                # not see it.
                if not isinstance(node, (ast.List, ast.Tuple)):
                    continue
                if len(node.elts) != 2:
                    continue
                key, value = node.elts
                if (isinstance(key, ast.Constant)
                        and key.value in self.CONTROLS
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, (int, float))):
                    offences.append(
                        f"{path.name}:{node.lineno} sets {key.value!r} to "
                        f"{value.value!r} on its own"
                    )
            # `sl.set_global("quantize", 1.0)` is the same opinion again, in a
            # third shape. Matching only bracket literals meant the guard
            # scanned `measure-loop-alignment.py` and saw nothing in it.
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
                    fn, "id", "")
                if name not in ("set_global", "set_loop"):
                    continue
                for i, arg in enumerate(node.args[:-1]):
                    nxt = node.args[i + 1]
                    if (isinstance(arg, ast.Constant)
                            and arg.value in self.CONTROLS
                            and isinstance(nxt, ast.Constant)
                            and isinstance(nxt.value, (int, float))):
                        offences.append(
                            f"{path.name}:{node.lineno} sets {arg.value!r} to "
                            f"{nxt.value!r} via {name}()"
                        )
        self.assertEqual(offences, [], "\n".join(
            ["a quantize value written outside looper_timing.engine_controls:"]
            + offences))

    def test_the_launch_path_asks_rather_than_decides(self) -> None:
        src = inspect.getsource(slot_runtime.SlotRuntime._execute_slot_ops)
        self.assertIn("timing.when(", src)
        self.assertNotIn("_session_sounding()", src,
                         "the launch branch is deciding for itself again")

    def test_the_gesture_path_asks_rather_than_decides(self) -> None:
        """Scoped to the RECORD_START branch, not the module.

        This was `assertIn("timing.when(", getsource(loop_model))` -- a
        substring search over the whole file, satisfied by the unrelated
        RECORD_CLOSE call forty lines away. It would have passed for the entire
        period the record branch was deciding its own timing.
        """
        asked = _actions_asked_in(pathlib.Path(loop_model.__file__))
        self.assertIn("RECORD_START", asked,
                      "the record branch decides its own timing again")
        self.assertIn("RECORD_CLOSE", asked)

    def test_no_gesture_carries_a_quantize_flag_of_its_own(self) -> None:
        """The flag was set once at boot from MPE_SL_SYNC_MODE and never
        updated: the MODE, kept beside the truth instead of read from it."""
        params = inspect.signature(track_gesture.TrackGesture.__init__).parameters
        self.assertNotIn("quantized", params)
        self.assertNotIn(
            "quantized", inspect.signature(loop_model.plan_gesture).parameters)


class DivergenceTests(unittest.TestCase):
    """D0, pinned in both directions."""

    def test_D0_recording_into_silence_still_counts_in(self) -> None:
        """Launch obeys the law; record cannot, because the engine holds the
        wait and the engine does not know what is sounding.

        When this is fixed the test fails, and the rule's own comment has to
        be rewritten in the same commit. That is deliberate.
        """
        silent = timing.Session(grid=True, sounding=False)
        self.assertTrue(timing.when(timing.CLIP_LAUNCH, silent).immediate)
        self.assertFalse(
            timing.when(timing.RECORD_START, silent).immediate,
            "D0 looks fixed — update the RECORD_START rule and this test",
        )
        # And the same question asked of the CODE PATH, not just the table.
        # Asserting only the table let the two disagree: someone could fix the
        # branch and leave the row, or flip the row and leave the branch, and
        # this class would stay green either way.
        plan = loop_model.plan_gesture(
            edge="down",
            sl_state=SL_STATE_OFF,
            pending=None,
            grid_established=True,
            is_defining=False,
            sounding=False,
        )
        self.assertEqual(plan.commands, ("record",))
        self.assertFalse(
            plan.arm_grid,
            "a grid exists, so this take is not the defining one",
        )

    def test_every_divergence_explains_itself(self) -> None:
        found = timing.divergences()
        self.assertIn(timing.RECORD_START, found)
        for action, text in found.items():
            with self.subTest(action=action):
                self.assertGreater(len(text), 30,
                                   "a divergence with no explanation is a bug "
                                   "someone will delete as noise")


if __name__ == "__main__":
    unittest.main()


class ReachabilityTests(unittest.TestCase):
    """Rows have to be REACHED. This is what `_assert_total` could not ask.

    The import-time check used to diff `ACTIONS` against `RULES` — two literals
    in one file — so it proved the author agreed with themselves. It stayed
    green while `SCENE_LAUNCH` sat in the table describing behaviour the code
    does not have, never asked for by anything, for as long as it existed.
    """

    #: Every production file that may ask a timing question.
    SOURCES = ("loop_model.py", "slot_runtime.py", "track_gesture.py",
               "slot_surface.py", "looper_songs.py", "sl_grid_sync.py")

    def _asked_anywhere(self) -> set[str]:
        root = pathlib.Path(timing.__file__).parent
        asked: set[str] = set()
        for name in self.SOURCES:
            path = root / name
            if path.exists():
                asked |= _actions_asked_in(path)
        return asked

    def test_every_bench_enforced_rule_is_actually_asked(self) -> None:
        """A bench-enforced rule that nothing asks is fiction.

        The engine cannot enforce these — by definition, they are the ones we
        hold. So if no call site queries the row, nothing implements it, and
        the row is a description of an instrument we did not build.
        """
        asked = self._asked_anywhere()
        by_name = {v: k for k, v in vars(timing).items()
                   if isinstance(v, str) and v in timing.RULES}
        for action, rule in timing.RULES.items():
            if rule.enforced_by != timing.BY_BENCH:
                continue
            with self.subTest(action=action):
                self.assertIn(
                    by_name[action], asked,
                    f"{action!r} is enforced by the bench and no call site "
                    f"asks about it — nothing implements this row",
                )

    def test_engine_enforced_rules_are_carried_by_the_controls(self) -> None:
        """The engine's half needs no branch, but it does need the controls."""
        controls = timing.engine_controls(grid=True)
        self.assertTrue(
            any(v for k, v in controls.items() if k != "round"),
            "no control is on with a grid, so nothing defers the engine's half",
        )

    def test_a_rule_nobody_asks_and_nothing_enforces_is_refused(self) -> None:
        """The check is not vacuous: a fictional bench row must fail it."""
        ghost = "scene launch"
        with self.assertRaises(AssertionError):
            asked = self._asked_anywhere()
            rules = dict(timing.RULES)
            rules[ghost] = timing.Rule(
                gate=timing.WHILE_SOUNDING,
                lands_on=timing.TRACK_WRAP,
                enforced_by=timing.BY_BENCH,
                why="many launches sharing one boundary",
            )
            self.assertIn("SCENE_LAUNCH", asked, "ghost row is unreachable")
