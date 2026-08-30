"""One owner for "is this take on disk", one owner for a loop's level.

Charter §3, question 3: *"How would you know if that were violated?"* For
everything in this file the honest answer was **you would not** — not from the
surface, not from the log, not from the suite. Each defect below is a reading
that is identical whether it worked or not.

  * **The flush.** `SlotRuntime._flush` was `dict[int, ...]` keyed by **loop**,
    and the value's first element was the **slot**: the code knew the slot
    mattered and never compared it. `_ensure_flushed(loop)` asked about the
    track's ACTIVE slot, saw `loop in self._flush` for some OTHER slot, declined
    to start a save, resolved the other slot's job and returned `clean`. The
    caller was the guard whose own message is *"REFUSING to switch — the take on
    the current slot did not reach disk, and switching would overwrite the
    buffer holding it"*. The wrong key routed around it. The pad lights, the
    model says saved, the take is gone.

  * **The reset.** `reset()` cleared four fields by name and `_flush` was a
    fifth. A save in flight through a clear-all completed afterwards, renaming
    its temp over a clip path on a track the model believed empty — and,
    combined with the key, marked the NEXT take clean without ever writing it.

  * **The level.** `scripts/sooperlooper/README.md` said of `loop_mix.wet_for()`
    that "nothing else ever writes `wet`". `looper_songs.load_song` writes it
    directly. A false invariant in a document is worse than no invariant,
    because the next reader budgets no attention for it.

None of these could be caught by asserting on one writer's outgoing messages,
which is how a suite of 1680 sat through all three. So the tests here read the
SOURCE and fail naming the file and line, or drive the runtime through the
sequence a player actually performs.

Every check carries its own non-vacuity guard — a positive control asserting
the thing that IS allowed is still found — so none can pass by finding nothing.
"""

from __future__ import annotations

import ast
import copy
import inspect
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))

from loop_mix import FaderMode, LoopMix, PARAMETERS  # noqa: E402
from slot_flush import (  # noqa: E402
    FLUSH_CLEAN,
    FLUSH_PENDING,
    FlushLedger,
)
from slot_matrix import ACT_NOOP, Slot, Track  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from sl_loop_states import SL_STATE_PLAYING  # noqa: E402

RUNTIME_SRC = REPO / "scripts" / "sooperlooper" / "slot_runtime.py"
FLUSH_SRC = REPO / "scripts" / "sooperlooper" / "slot_flush.py"

#: Every python file that can talk to the looper engine. Same set as
#: `test_clock_tail_ownership.py` uses, for the same reason: a shell script
#: could `oscsend` past all of this, and none does today.
ENGINE_SOURCES = sorted(
    set((REPO / "scripts" / "sooperlooper").glob("*.py"))
    | set((REPO / "scripts").glob("*.py"))
    | set((REPO / "scripts" / "lib").glob("*.py"))
    | set((REPO / "patch_browser").glob("*.py"))
)

#: The one place a per-loop level is composed.
WET_COMPOSER = "loop_mix.py"

#: The one sanctioned direct write, as (file, enclosing function). Songs are
#: loaded by `touch-patch-browser.service`; `LoopMix` lives in
#: `mpe-looper-session.service`. The composer's state — every column gain, the
#: master, the active-loop count — is in the other process, so this call cannot
#: reach the seam. `LoopMix.seed_from_engine` adopts the value afterwards.
#: See `scripts/sooperlooper/README.md`, "One writer, with one named exception".
WET_EXCEPTION = ("looper_songs.py", "load_song")


# --- AST helpers ------------------------------------------------------------


def _self_attr(node: ast.AST) -> str | None:
    """`self._foo` -> `"_foo"`, anything else -> None."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def _class_node(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name}: no class {name}")


def _methods(cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {
        child.name: child
        for child in cls.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _attrs_assigned_in(fn: ast.AST) -> set[str]:
    """`self._foo = ...` / `self._foo: T = ...` inside one function."""
    out: set[str] = set()
    for node in ast.walk(fn):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            name = _self_attr(target)
            if name is not None:
                out.add(name)
    return out


def _attrs_mutated_in_place(cls: ast.ClassDef) -> dict[str, int]:
    """Attributes the class changes without rebinding, name -> first line.

    Two shapes, and they are the only two that have ever existed here:
    `self._foo[key] = value` / `del self._foo[key]`, and `self._foo.method(...)`.
    Rebinding `self._foo` itself is creation, not mutation, and is what
    `reset()` is for.

    `self._log(...)` and friends are calls on the attribute, not calls on a
    method OF the attribute, so configuration callables do not trip this.
    """
    out: dict[str, int] = {}
    for node in ast.walk(cls):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AugAssign)
            else node.targets if isinstance(node, ast.Delete)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Subscript):
                name = _self_attr(target.value)
                if name is not None:
                    out.setdefault(name, node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = _self_attr(node.func.value)
            if name is not None:
                out.setdefault(name, node.lineno)
    return out


def _is_set_path(node: ast.AST) -> bool:
    """True for an OSC path that writes a control: `/set`, `/sl/N/set`, f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.endswith("/set")
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        return (
            isinstance(last, ast.Constant)
            and isinstance(last.value, str)
            and last.value.endswith("/set")
        )
    return False


def _path_is_unreadable(node: ast.AST) -> bool:
    """True when the OSC path is an expression we cannot evaluate statically.

    A variable path could be anything, `/set` included, so it counts as a write
    rather than being waved through. Keeps `self._send(path, ["wet", v])` from
    being an escape hatch.
    """
    return not isinstance(node, (ast.Constant, ast.JoinedStr))


#: Call names that put a message on the wire in this repo.
SEND_CALLEES = frozenset({"send", "send_message", "_send"})


def _wet_writes(path: Path) -> list[tuple[str, int]]:
    """Every `send(<a /set path>, ["wet", ...])` in one file, with its function.

    Keyed on the PATH as well as the payload, deliberately: the bench
    *subscribes* to `wet` (`sl_osc_session.py`,
    `send_message("/sl/N/register_auto_update", ["wet", ...])`) and that is a
    read. It is excluded by its path, by shape — not by an exemption naming it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing(node: ast.AST) -> str:
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(cur)
        return "<module>"

    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        callee = (
            node.func.id if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute)
            else None
        )
        if callee not in SEND_CALLEES:
            continue
        if not (_is_set_path(node.args[0]) or _path_is_unreadable(node.args[0])):
            continue
        payload = node.args[1]
        if not isinstance(payload, ast.List) or not payload.elts:
            continue
        first = payload.elts[0]
        if isinstance(first, ast.Constant) and first.value == "wet":
            out.append((enclosing(node), node.lineno))
    return out


# --- the silent take loss ---------------------------------------------------


class SilentTakeLossTests(unittest.TestCase):
    """The regression this stage exists for, driven as a player would reach it.

    No private state is seeded past the opening position. Every step is a call
    the surface makes in response to a pad, and the assertions are about audio
    and files, not about dictionaries.
    """

    def setUp(self) -> None:
        self.clock = [0.0]
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.sent: list[tuple[str, list]] = []
        self.logs: list[str] = []
        self.sounding = [True]
        #: Does the engine finish writing a save_loop temp? Off means the save
        #: is genuinely still in flight, which is the whole scenario.
        self.engine_writes = False
        #: Every take gets a distinguishable payload so a resurrected one can
        #: be told from a fresh one by reading the bytes.
        self.take = 0
        self.rt = SlotRuntime(
            send=self._send,
            clips_dir=self.dir,
            num_tracks=15,
            log=self.logs.append,
            now=lambda: self.clock[0],
            session_sounding=lambda: self.sounding[0],
        )

    def _send(self, path: str, args: list) -> None:
        self.sent.append((path, args))
        if path.endswith("/save_loop") and self.engine_writes:
            self._write_take(Path(args[0]))

    def _write_take(self, tmp: Path) -> None:
        self.take += 1
        tmp.write_bytes(f"take{self.take}".encode() + b"\0" * 4096)

    def _saved_temps(self) -> list[str]:
        return [
            Path(args[0]).name
            for path, args in self.sent
            if path.endswith("/save_loop")
        ]

    def test_a_save_stranded_on_one_slot_cannot_answer_for_another(self) -> None:
        """The headline. Seven presses, no reset, one lost take.

        Old behaviour at step 7: `0 in self._flush` was true because of the
        stranded slot-1 job, so no save was started for slot 2's take; the poll
        resolved the slot-1 job instead, renamed its temp over slot 1's path —
        resurrecting the clip cleared at step 3 — and returned `clean`. The
        press then proceeded to reuse the buffer holding slot 2's take.
        """
        # 1. Track 1 plays slot 1's take. It is not on disk yet.
        self.rt._tracks[0] = Track(
            slots=(
                Slot(
                    file="live_t00_s0.wav",
                    len_s=2.0,
                    sl_state=SL_STATE_PLAYING,
                    dirty=True,
                ),
                *([None] * 7),
            ),
            active_slot=0,
        )

        # 2. Arm slot 2. The outgoing take has to reach disk first, so the
        #    press parks behind a save. The engine has not written it.
        parked = self.rt.press(0, 1, sl_state=SL_STATE_PLAYING)
        self.assertEqual(parked.action, ACT_NOOP)
        self.assertEqual(self.rt.awaiting_tracks(), (0,))
        self.assertEqual(
            self._saved_temps(), ["live_t00_s0.wav.part"], "slot 1 is saving"
        )

        # 3. Long-press the lit pad: hold-to-clear. `TrackGesture` clears the
        #    engine; the runtime drops the file and the binding, and `abandon`
        #    drops the parked press — so nothing will poll that save again.
        self.assertTrue(self.rt.forget_active_slot(0))
        self.assertEqual(self.rt.awaiting_tracks(), ())

        # 4. The engine finishes the temp anyway. It was asked for it, and the
        #    write is not ours to cancel.
        self._write_take(self.dir / "live_t00_s0.wav.part")

        # 5-6. Record a new take into slot 2 and let it land.
        self.rt.press(0, 1, sl_state=SL_STATE_PLAYING)
        self.rt.mark_recorded(0, 1, len_s=2.0, sl_state=SL_STATE_PLAYING)
        self.assertTrue(self.rt.track(0).slot(1).dirty)

        # 7. Arm slot 3. This is the question that used to be answered about
        #    the wrong slot.
        plan = self.rt.press(0, 2, sl_state=SL_STATE_PLAYING)

        self.assertEqual(
            plan.action,
            ACT_NOOP,
            "the press must park behind slot 2's own save, not proceed on a "
            "`clean` that was about slot 1",
        )
        self.assertIn("waiting for save", plan.note)
        self.assertIn(
            "live_t00_s1.wav.part",
            self._saved_temps(),
            "no save was ever started for the take actually in the buffer",
        )
        self.assertTrue(
            self.rt.track(0).slot(1).dirty,
            "slot 2 was reported saved without anything having saved it",
        )
        self.assertFalse(
            (self.dir / "live_t00_s0.wav").exists(),
            "the clip cleared at step 3 came back: a stranded save renamed its "
            "temp over the path after the player deleted it",
        )

    def test_a_save_in_flight_does_not_outlive_a_clear_all(self) -> None:
        """Reset half. The pre-reset take must not become the post-reset file."""
        self.rt._tracks[0] = Track(
            slots=(
                Slot(
                    file="live_t00_s0.wav",
                    len_s=2.0,
                    sl_state=SL_STATE_PLAYING,
                    dirty=True,
                ),
                *([None] * 7),
            ),
            active_slot=0,
        )
        self.rt.press(0, 1, sl_state=SL_STATE_PLAYING)
        self.assertEqual(self.rt.awaiting_tracks(), (0,), "a save is in flight")

        # Shift + Stop All, held: clear everything.
        self.rt.reset()

        # The engine finishes the pre-reset save afterwards.
        self._write_take(self.dir / "live_t00_s0.wav.part")

        # A fresh take is recorded into the same cell and saved for real.
        self.engine_writes = True
        self.rt.press(0, 0, sl_state=SL_STATE_PLAYING)
        self.rt.mark_recorded(0, 0, len_s=2.0, sl_state=SL_STATE_PLAYING)
        self.rt.press(0, 1, sl_state=SL_STATE_PLAYING)

        clip = self.dir / "live_t00_s0.wav"
        self.assertTrue(clip.exists(), "the new take reached disk")
        self.assertTrue(
            clip.read_bytes().startswith(b"take2"),
            "the bytes on disk are the PRE-RESET take: a stranded job renamed "
            "its temp over the clip and marked the new take clean",
        )

    def test_a_superseded_take_cannot_answer_for_the_one_that_replaced_it(self) -> None:
        """The same cell, twice, with the first save still in flight.

        Found by auditing Stage 4b rather than by the suite: the whole tree of
        1696 tests passed with `mark_recorded`'s `drop()` deleted. The line was
        right and its comment named the exact failure — "rename those stale
        bytes over the clip path and report the NEW take clean without ever
        having written it" — and nothing guarded it.

        Keying the ledger by `(loop, slot)` does not cover this on its own: both
        takes are the SAME cell, so the key matches and the superseded job
        answers for its replacement. Only dropping the promise when the audio it
        was about is replaced closes it.
        """
        # 1. A take lands on track 1, slot 1 and a save goes out for it. The
        #    engine has not finished writing, so the save is genuinely pending.
        self.rt.mark_recorded(0, 0, len_s=2.0, sl_state=SL_STATE_PLAYING)
        status, slot = self.rt._ensure_flushed(0)
        self.assertEqual((status, slot), (FLUSH_PENDING, 0))
        first_save = len(self._saved_temps())

        # 2. The player records over that same cell. The buffer now holds a
        #    different take; the promise made about the old one is void.
        self.rt.mark_recorded(0, 0, len_s=3.0, sl_state=SL_STATE_PLAYING)

        # 3. The engine finishes the FIRST save, late — its temp appears now,
        #    holding the superseded audio.
        self._write_take(self.rt.clip_path(0, 0).with_name(
            self.rt.clip_path(0, 0).name + ".part"
        ))

        # 4. Ask about the cell, as any of the three flush-gated presses does.
        status, slot = self.rt._ensure_flushed(0)

        self.assertTrue(
            self.rt.track(0).slot(0).dirty,
            "the take now in the buffer was never written, so the cell must "
            "still read dirty; reporting it clean is the silent loss with the "
            "key defect fixed and the promise left standing",
        )
        self.assertNotEqual(
            status, FLUSH_CLEAN,
            "a save begun for the SUPERSEDED take cannot answer for the one "
            "that replaced it — same cell, different audio",
        )
        self.assertGreater(
            len(self._saved_temps()), first_save,
            "the replacement take needs a save of its own; if none was sent, "
            "the only bytes anywhere are the superseded take's",
        )
        clip = self.rt.clip_path(0, 0)
        if clip.exists():
            self.assertNotIn(
                b"take1", clip.read_bytes()[:16],
                "the superseded take was promoted onto the clip path",
            )


# --- the ledger's shape -----------------------------------------------------


class FlushLedgerApiTests(unittest.TestCase):
    """The wrong question must be unaskable, not merely unasked.

    A `poll_flush(loop)` that returns a verdict is the defect in API form. This
    walks `FlushLedger`'s public signatures and fails if any method that takes
    a loop does not also take a slot — so the shape cannot come back, whatever
    it is named.
    """

    def _public_methods(self) -> dict[str, inspect.Signature]:
        return {
            name: inspect.signature(member)
            for name, member in inspect.getmembers(FlushLedger, inspect.isfunction)
            if not name.startswith("_")
        }

    def test_the_scan_finds_the_ledgers_methods(self) -> None:
        """Guard. If this came back empty the rule below would check nothing."""
        found = set(self._public_methods())
        self.assertEqual(
            found,
            {"begin", "drop", "in_flight", "poll", "running"},
            "FlushLedger's public surface changed; the (loop, slot) rule has "
            "to cover the new method",
        )

    def test_no_method_takes_a_loop_without_a_slot(self) -> None:
        offenders = []
        keyed = []
        for name, sig in sorted(self._public_methods().items()):
            params = set(sig.parameters)
            if "loop" not in params:
                continue
            keyed.append(name)
            if "slot" not in params:
                offenders.append(f"{FLUSH_SRC.name}: FlushLedger.{name}{sig}")
        self.assertEqual(
            offenders,
            [],
            "a per-track question about a per-cell fact is how a take was "
            "lost — the answer would be about whichever slot the ledger "
            "happened to hold:\n" + "\n".join(offenders),
        )
        self.assertTrue(
            keyed, "no method takes a loop at all — the check found nothing"
        )

    def test_a_save_on_one_cell_is_not_an_answer_about_another(self) -> None:
        """The property the signatures exist to protect, driven."""
        tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        clock = [0.0]
        ledger = FlushLedger(
            send=lambda _p, _a: None, now=lambda: clock[0], log=lambda _m: None
        )
        ledger.begin(0, 0, tmpdir / "a.wav", timeout_s=2.0)
        self.assertEqual(ledger.poll(0, 0), FLUSH_PENDING)
        self.assertFalse(ledger.running(0, 1))
        self.assertEqual(
            ledger.poll(0, 1),
            FLUSH_CLEAN,
            "slot 1 has no save; slot 0's must not report for it either way",
        )
        self.assertEqual(ledger.in_flight(), ((0, 0),))


# --- one owner of the on-disk question --------------------------------------


class OnDiskOwnershipTests(unittest.TestCase):
    """Who may say a take is on disk, and who may put it there."""

    def _dirty_writers(self) -> dict[str, list[int]]:
        """Every `dirty=` passed to a call, by file.

        The dataclass field declaration in `slot_matrix.py` is an annotated
        assignment, not a call, so it is not a writer and does not need
        exempting by name.
        """
        out: dict[str, list[int]] = {}
        for path in ENGINE_SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg == "dirty":
                        out.setdefault(path.name, []).append(node.lineno)
        return out

    def test_only_the_runtime_writes_the_dirty_flag(self) -> None:
        writers = self._dirty_writers()
        offenders = [
            f"{name}:{line}"
            for name, lines in sorted(writers.items())
            if name != RUNTIME_SRC.name
            for line in lines
        ]
        self.assertEqual(
            offenders,
            [],
            "`Slot.dirty` is the record of whether a take reached disk. A "
            "second writer of it is a second answer to that question:\n"
            + "\n".join(offenders),
        )
        self.assertGreaterEqual(
            len(writers.get(RUNTIME_SRC.name, [])),
            2,
            "positive control: the owner must both SET dirty (a take landed) "
            "and CLEAR it (the save landed). Fewer than two sites means the "
            "scan is not finding them",
        )

    def test_only_the_ledger_renames_onto_a_clip_path(self) -> None:
        """`os.replace` inside the looper package, which is where clips live.

        Scoped to `scripts/sooperlooper/` deliberately: `patch_browser` uses
        the same rename-over-temp idiom for JSON state, which is a different
        file and a different owner.
        """
        found: list[str] = []
        for path in sorted((REPO / "scripts" / "sooperlooper").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "replace"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ):
                    found.append(f"{path.name}:{node.lineno}")
        self.assertEqual(
            [site for site in found if not site.startswith(FLUSH_SRC.name)],
            [],
            "publishing a recorded take is the ledger's job — a second site "
            "is a second moment at which a clip file can change under the "
            "model:\n" + "\n".join(found),
        )
        self.assertTrue(
            found, "positive control: the owner does rename, and it was not found"
        )


# --- reset cannot forget a field --------------------------------------------


class ResetOwnershipTests(unittest.TestCase):
    """`reset()` is the sole constructor of `SlotRuntime`'s mutable state.

    Enumerating fields to clear is a list that goes stale. Constructing them is
    not: a field `reset()` does not create does not exist, so forgetting one is
    an `AttributeError` on the first press rather than state that survives a
    clear-all. This holds the class to that.
    """

    def setUp(self) -> None:
        self.cls = _class_node(RUNTIME_SRC, "SlotRuntime")
        self.methods = _methods(self.cls)

    def test_init_delegates_to_reset(self) -> None:
        calls = {
            node.func.attr
            for node in ast.walk(self.methods["__init__"])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        }
        self.assertIn(
            "reset",
            calls,
            f"{RUNTIME_SRC.name}: __init__ must build its mutable state by "
            "calling reset(), or the two enumerations start to differ",
        )

    def test_the_scan_finds_the_state_it_thinks_it_does(self) -> None:
        """Guard. An empty scan would make the rule below pass by default."""
        self.assertEqual(
            set(_attrs_mutated_in_place(self.cls)),
            {"_awaiting", "_deferred", "_flush", "_grid_wait", "_tracks"},
            "SlotRuntime's mutable state changed. Whatever was added has to be "
            "created in reset() and named here, so someone looks at it",
        )

    def test_every_mutated_field_is_created_by_reset(self) -> None:
        created = _attrs_assigned_in(self.methods["reset"])
        offenders = [
            f"{RUNTIME_SRC.name}:{line} — self.{name} is mutated but reset() "
            f"never creates it, so a clear-all leaves it holding the old session"
            for name, line in sorted(_attrs_mutated_in_place(self.cls).items())
            if name not in created
        ]
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_init_creates_no_state_that_reset_also_creates(self) -> None:
        """One birthplace, not two that can drift apart."""
        both = _attrs_assigned_in(self.methods["__init__"]) & _attrs_assigned_in(
            self.methods["reset"]
        )
        self.assertEqual(
            both,
            set(),
            f"{RUNTIME_SRC.name}: these are built in both __init__ and "
            f"reset(): {sorted(both)}",
        )

    def test_reset_leaves_a_runtime_indistinguishable_from_a_fresh_one(self) -> None:
        """The same rule driven rather than read.

        The AST check is the deterministic gate; this is the one that would
        notice a field that is cleared *wrongly* rather than not at all.
        """
        tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        clock = [0.0]
        rt = SlotRuntime(
            send=lambda _p, _a: None,
            clips_dir=tmpdir,
            num_tracks=4,
            log=lambda _m: None,
            now=lambda: clock[0],
            session_sounding=lambda: True,
            grid_boundary=lambda: 1.0,
        )
        # Deep-copied, not referenced. Holding the live dicts would make the
        # comparison below compare each field with itself — which the positive
        # control caught the first time this test was written.
        fresh = {
            name: copy.deepcopy(value)
            for name, value in vars(rt).items()
            if name != "_flush"
        }

        # Populate every field: a take in the buffer, a save in flight behind a
        # parked press, and a deferred launch waiting on the grid.
        (tmpdir / "live_t01_s1.wav").write_bytes(b"\0" * 4096)
        rt._tracks[0] = Track(
            slots=(
                Slot("live_t00_s0.wav", 2.0, SL_STATE_PLAYING, dirty=True),
                *([None] * 7),
            ),
            active_slot=0,
        )
        rt.press(0, 1, sl_state=SL_STATE_PLAYING)
        rt._tracks[1] = Track(
            slots=(None, Slot("live_t01_s1.wav", 2.0, SL_STATE_PLAYING), *([None] * 6)),
            active_slot=None,
        )
        rt.press(1, 1, sl_state=SL_STATE_PLAYING)

        # Positive control: without this the comparison below is empty == empty.
        dirty_before = {
            "_tracks": rt._tracks != fresh["_tracks"],
            "_deferred": bool(rt._deferred),
            "_awaiting": bool(rt._awaiting),
            "_grid_wait": bool(rt._grid_wait),
            "_flush": bool(rt._flush.in_flight()),
        }
        self.assertEqual(
            [name for name, moved in dirty_before.items() if not moved],
            [],
            "positive control: these fields were still empty after driving the "
            "runtime, so resetting them proves nothing",
        )

        rt.reset()

        for name, value in fresh.items():
            self.assertEqual(
                value, getattr(rt, name), f"reset() left {name} behind"
            )
        self.assertEqual(
            rt._flush.in_flight(),
            (),
            "a save in flight through a clear-all lands afterwards, renaming "
            "its temp over a clip path on a track the model believes empty",
        )


# --- one composer of a loop's level -----------------------------------------


class WetOwnershipTests(unittest.TestCase):
    """`wet` has one composer and one documented exception. Not two writers.

    The README claimed "nothing else ever writes `wet`" while
    `looper_songs.load_song` did exactly that. The claim is now the narrower one
    that is true, and this is what holds the code to it.
    """

    def _all_wet_writes(self) -> list[tuple[str, str, int]]:
        out: list[tuple[str, str, int]] = []
        for path in ENGINE_SOURCES:
            for function, line in _wet_writes(path):
                out.append((path.name, function, line))
        return sorted(out)

    def test_the_detector_finds_the_write_we_know_about(self) -> None:
        """Guard. The scan looks for a literal `"wet"` payload on a `/set`
        path; if that stopped matching, the rule below would find nothing and
        pass while a second writer sat in the tree."""
        found = self._all_wet_writes()
        self.assertIn(
            (WET_EXCEPTION[0], WET_EXCEPTION[1]),
            [(name, function) for name, function, _line in found],
            "the known cross-process write in looper_songs.load_song is not "
            "being detected, so this scan is measuring nothing",
        )

    def test_the_subscription_is_not_mistaken_for_a_write(self) -> None:
        """`register_auto_update ["wet", ...]` is a read, excluded by its path
        rather than by an exemption naming the file."""
        self.assertEqual(
            [
                (name, function, line)
                for name, function, line in self._all_wet_writes()
                if name == "sl_osc_session.py"
            ],
            [],
        )

    def test_no_module_writes_wet_outside_the_composer(self) -> None:
        offenders = [
            f"{name}:{line} in {function}()"
            for name, function, line in self._all_wet_writes()
            if name != WET_COMPOSER and (name, function) != WET_EXCEPTION
        ]
        self.assertEqual(
            offenders,
            [],
            "a loop's level is composed in loop_mix.wet_for() — user gain x "
            "master x auto-law, recomputed in full — and a second writer means "
            "which level you get depends on call order:\n" + "\n".join(offenders),
        )

    def test_the_composer_really_does_emit_a_wet_write(self) -> None:
        """Positive control, and the reason the scan above cannot simply
        require the composer to appear in it.

        `LoopMix` never writes the string `"wet"`: it emits
        `[PARAMETERS[mode].control, wet_for(loop)]`, so the AST scan cannot see
        it and a check written as "the composer must be in the results" would
        have failed for the wrong reason. Driving it is the honest control.
        """
        self.assertEqual(PARAMETERS[FaderMode.LEVEL].control, "wet")
        mix = LoopMix(num_loops=4)
        mix.messages_for(0, 100)  # first touch anchors, sends nothing
        messages = mix.messages_for(0, 110)
        self.assertTrue(messages, "the composer emitted nothing to control")
        for path, args in messages:
            self.assertTrue(path.endswith("/set"))
            self.assertEqual(args[0], "wet")
            self.assertEqual(args[1], mix.wet_for(int(path.split("/")[2])))


if __name__ == "__main__":
    unittest.main()
