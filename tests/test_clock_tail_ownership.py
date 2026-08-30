"""One owner for the clock, one owner for the ring-out — the invariants.

Charter §3, question 3: *"How would you know if that were violated?"* For both
of the things in this file the honest answer was **you would hear it**, once, in
a take you cannot get back:

  * **The tail.** `_end_tail` is read-guard-clear and sends `hit overdub`, which
    is a TOGGLE. Four sites could reach it from an OSC dispatcher thread while
    `poll_tail` ran it from the main loop at ~485 Hz. Both threads pass the
    guard, both send, the first ends the overdub and the second starts a new
    one — recording room tone over the take, behind a green pad. The narrowing
    `sl_state == OVERDUBBING` check cannot close it, because `sl_state` is
    written by the same OSC thread that is racing.

  * **The clock.** `Engine::set_tempo` zeroes `_quarter_counter` and
    `_tempo_counter` (engine.cpp:2174-2178), so **sending the tempo IS the phase
    reset** (`scripts/sooperlooper/README.md`, Clock). Four places sent it, and
    three of them differed: one forgot the matching `mark_phase_zero`, one
    passed a stale bar count, one skipped `smart_eighths`/`eighth_per_cycle`
    entirely. A grid the engine and the bench disagree about is silent — every
    launch simply lands somewhere nobody chose.

Neither could be caught by asserting on one writer's outgoing messages, which
is how a suite of 1660 sat through both. So the two tests that matter here read
the SOURCE and fail naming the file and line:

  * `TailOwnershipTests` — no method the OSC listener can call may reach a tail
    mutator, checked over the call graph inside `TrackGesture`.
  * `ClockOwnershipTests` — `establish_grid_clock` has exactly one caller, and
    no module outside `sl_grid_sync` writes the engine's tempo at all.

Both carry their own non-vacuity proof in the suite: a test asserting the owner
DOES reach what everyone else may not, so the check cannot pass by finding
nothing.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))

from looper_songs import (  # noqa: E402
    build_manifest_v2,
    load_song,
    manifest_path,
    parse_manifest,
    save_song,
)
from sl_grid_state import GridState, derive_tempo  # noqa: E402
from sl_grid_sync import (  # noqa: E402
    EIGHTH_PER_CYCLE,
    apply_established_grid,
)
from sl_loop_states import (  # noqa: E402
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
)
from track_gesture import TrackGesture, poll_track_gestures  # noqa: E402

GESTURE_SRC = REPO / "scripts" / "sooperlooper" / "track_gesture.py"
LISTENER_SRC = REPO / "scripts" / "sooperlooper" / "sl_bench_listener.py"
GRID_SYNC_SRC = REPO / "scripts" / "sooperlooper" / "sl_grid_sync.py"

#: Every python file that can talk to the looper engine. Python only — a shell
#: script could `oscsend` a tempo past this, and none does today.
ENGINE_SOURCES = sorted(
    set((REPO / "scripts" / "sooperlooper").glob("*.py"))
    | set((REPO / "scripts").glob("*.py"))
    | set((REPO / "scripts" / "lib").glob("*.py"))
    | set((REPO / "patch_browser").glob("*.py"))
)

#: The three methods that may create, end or abandon a ring-out. They are the
#: only code allowed to assign `self._tail`, and only `poll_tail` may call them.
TAIL_MUTATORS = frozenset({"_begin_tail", "_end_tail", "_abandon_tail"})

#: Plus the constructor, which establishes the field.
TAIL_ASSIGNERS = TAIL_MUTATORS | {"__init__"}

#: The owner. One method, on one thread.
TAIL_OWNER = "poll_tail"

#: The take Mitch caught in review on 2026-08-30 — 6.939 s, read as 4 bars.
#: `(bpm, bars)` derived rather than written down, because the tempo is exact
#: and never rounded: 138 BPM is 17 ms a cycle away from this take, and that
#: drift is what makes a defining clip walk away from every later one.
def _four_bar_take() -> tuple[float, int]:
    return derive_tempo(6.939)


# --- AST helpers ------------------------------------------------------------


def _methods_of(path: Path, class_name: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name: child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"{path.name}: no class {class_name}")


def _self_calls(fn: ast.AST) -> list[tuple[str, int]]:
    """`self.foo(...)` sites inside one function, with their line numbers."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            out.append((func.attr, node.lineno))
    return out


def _reaches(
    methods: dict[str, ast.FunctionDef], start: str, targets: frozenset[str]
) -> list[str] | None:
    """A call chain from `start` to any of `targets`, or None. Breadth-first."""
    seen = {start}
    queue: list[tuple[str, list[str]]] = [(start, [start])]
    while queue:
        name, path = queue.pop(0)
        fn = methods.get(name)
        if fn is None:
            continue
        for callee, lineno in _self_calls(fn):
            step = f"{callee} ({GESTURE_SRC.name}:{lineno})"
            if callee in targets:
                return path + [step]
            if callee in seen or callee not in methods:
                continue
            seen.add(callee)
            queue.append((callee, path + [step]))
    return None


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


def _tempo_writes(path: Path) -> list[str]:
    """Every `send(<...>/set, ["tempo", ...])` in one file.

    Deliberately keyed on the PATH as well as the control name: subscribing to
    tempo (`/register_auto_update`, `["tempo", 200, returl, "/r"]`) is a read
    and must not trip this. Writing it is the phase reset.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if not _is_set_path(node.args[0]):
            continue
        payload = node.args[1]
        if not isinstance(payload, ast.List) or not payload.elts:
            continue
        first = payload.elts[0]
        if isinstance(first, ast.Constant) and first.value == "tempo":
            out.append(f"{path.name}:{node.lineno}")
    return out


def _calls_named(path: Path, name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        if called == name:
            out.append(f"{path.name}:{node.lineno}")
    return out


# --- the tail ---------------------------------------------------------------


class TailOwnershipTests(unittest.TestCase):
    """`self._tail` has one owner: `poll_tail`, on the bench's idle loop.

    What the instrument sees, stated plainly: it walks `self.<method>()` calls
    inside `TrackGesture` from each entry point `SlBenchStateListener` can
    invoke, and fails if any chain reaches `_begin_tail`, `_end_tail` or
    `_abandon_tail`. A mutation reached through a callback, a `getattr`, or a
    free function taking a gesture would get through. It catches every form
    that has actually existed here — all four historical sites were direct
    `self._end_tail(...)` / `self._begin_tail()` calls.
    """

    def setUp(self) -> None:
        self.methods = _methods_of(GESTURE_SRC, "TrackGesture")

    def _listener_entry_points(self) -> set[str]:
        """Method names `SlBenchStateListener` calls that exist on a gesture.

        Read out of the listener rather than hardcoded, so routing a NEW engine
        control to the gesture puts it under this rule automatically.
        """
        tree = ast.parse(LISTENER_SRC.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in self.methods:
                    names.add(node.func.attr)
        return names

    def test_the_osc_entry_points_are_the_ones_we_think(self) -> None:
        """Guards the extraction. If this ever came back empty the headline
        test below would pass by checking nothing at all."""
        found = self._listener_entry_points()
        self.assertEqual(
            found,
            {"sync_from_sl", "sync_loop_len", "sync_loop_pos", "sync_in_peak"},
            "the set of gesture methods reachable from an OSC dispatcher "
            "thread changed; the ownership rule has to cover the new one",
        )

    def test_no_osc_entry_point_reaches_a_tail_mutator(self) -> None:
        offenders = []
        for entry in sorted(self._listener_entry_points()):
            chain = _reaches(self.methods, entry, TAIL_MUTATORS)
            if chain is not None:
                offenders.append(" -> ".join(chain))
        self.assertEqual(
            offenders,
            [],
            "these run on an OSC dispatcher thread and mutate the ring-out, so "
            "two threads can both send the `overdub` TOGGLE and the second one "
            "starts a fresh overdub over the take:\n" + "\n".join(offenders),
        )

    def test_the_owner_does_reach_them(self) -> None:
        """Proof the check above is not vacuous — the seam has to be somewhere."""
        for mutator in sorted(TAIL_MUTATORS):
            chain = _reaches(self.methods, TAIL_OWNER, frozenset({mutator}))
            self.assertIsNotNone(
                chain, f"{TAIL_OWNER} no longer reaches {mutator}"
            )

    def test_the_tail_field_is_assigned_only_by_its_owner(self) -> None:
        """A second writer that skipped the helpers would evade the call graph."""
        offenders = []
        for name, fn in self.methods.items():
            if name in TAIL_ASSIGNERS:
                continue
            for node in ast.walk(fn):
                targets = (
                    node.targets if isinstance(node, ast.Assign)
                    else [node.target] if isinstance(node, ast.AnnAssign)
                    else []
                )
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "_tail"
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        offenders.append(
                            f"{GESTURE_SRC.name}:{node.lineno} in {name}()"
                        )
        self.assertEqual(offenders, [], "\n".join(offenders))


class TailUnderConcurrencyTests(unittest.TestCase):
    """The same rule, driven rather than read.

    This is the probabilistic net; `TailOwnershipTests` is the deterministic
    gate. A race that fires one time in fifty passes a run of this and is
    caught by the AST check on every run, which is the whole argument for
    removing the race by construction instead of guarding it.
    """

    def _gesture(self, osc, clock):
        fs = TrackGesture(
            loop=0, hold_ms=2000, debounce_ms=0, multigrid=True,
            now=lambda: clock[0],
        )
        fs.bind(osc, None, None)
        fs.loop_len = 2.0
        return fs

    def test_many_osc_threads_and_one_poller_send_one_overdub(self) -> None:
        for _ in range(25):
            osc = _RecordingOsc()
            clock = [0.0]
            fs = self._gesture(osc, clock)
            fs.sync_from_sl(SL_STATE_RECORDING)
            fs.sync_from_sl(SL_STATE_OVERDUBBING)
            poll_track_gestures([fs])
            self.assertTrue(fs.in_tail)
            osc.clear()

            ready = threading.Barrier(5)
            stop = threading.Event()

            def report() -> None:
                # One OSC dispatcher thread, doing what one does: hand the
                # gesture what the engine said. `ThreadingOSCUDPServer` gives
                # every datagram its own thread, so there are really this many.
                ready.wait()
                for _ in range(40):
                    fs.sync_in_peak(0.9)
                    fs.sync_loop_pos(1.9)
                    fs.sync_loop_pos(0.02)

            threads = [threading.Thread(target=report) for _ in range(4)]
            for t in threads:
                t.start()
            ready.wait()
            while any(t.is_alive() for t in threads) or not stop.is_set():
                poll_track_gestures([fs])
                if not any(t.is_alive() for t in threads):
                    stop.set()
            for t in threads:
                t.join()
            poll_track_gestures([fs])

            self.assertEqual(
                osc.hits().count("overdub"), 1,
                "a second `overdub` is a toggle: it starts a fresh overdub "
                "recording the room over the take",
            )
            self.assertFalse(fs.in_tail)


class RingOutCapTests(unittest.TestCase):
    """The cap is one CYCLE, end to end through the gesture.

    `looper-timing-model-spec.md` §6. Pinned here as well as in
    `test_tail_phase` because the regression was not in `cap_for`'s arithmetic
    — it was in what the caller handed it. `_begin_tail` passed `grid.bpm` and
    the cycle stopped being one bar underneath it on the same day.
    """

    def _gesture_with_grid(self, cycle_s: float, bpm: float, bars: int):
        grid = GridState()
        self.assertTrue(grid.restore(bpm, bars, cycle_s))
        clock = [0.0]
        fs = TrackGesture(
            loop=0, hold_ms=2000, debounce_ms=0, multigrid=True, grid=grid,
            now=lambda: clock[0],
        )
        fs.bind(_RecordingOsc(), None, None)
        fs.loop_len = cycle_s
        return fs, clock

    def test_a_four_bar_cycle_caps_at_the_whole_cycle(self) -> None:
        fs, _clock = self._gesture_with_grid(6.939, *_four_bar_take())
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.sync_from_sl(SL_STATE_OVERDUBBING)
        poll_track_gestures([fs])
        self.assertAlmostEqual(fs._tail.cap_s, 6.939)

    def test_it_is_not_the_bar(self) -> None:
        """The regression, as a number: one bar of a 4-bar 138 BPM cycle is
        1.735 s, a quarter of the ring-out the spec allows."""
        fs, _clock = self._gesture_with_grid(6.939, *_four_bar_take())
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.sync_from_sl(SL_STATE_OVERDUBBING)
        poll_track_gestures([fs])
        self.assertGreater(fs._tail.cap_s, (60.0 / 138.0) * 4 * 2)

    def test_the_defining_take_still_falls_back_to_its_own_length(self) -> None:
        """No grid yet — the first take is the one that makes one."""
        clock = [0.0]
        fs = TrackGesture(
            loop=0, hold_ms=2000, debounce_ms=0, multigrid=True,
            now=lambda: clock[0],
        )
        fs.bind(_RecordingOsc(), None, None)
        fs.loop_len = 4.078
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.sync_from_sl(SL_STATE_OVERDUBBING)
        poll_track_gestures([fs])
        self.assertAlmostEqual(fs._tail.cap_s, 4.078)


# --- the clock --------------------------------------------------------------


class ClockOwnershipTests(unittest.TestCase):
    """`GridState` owns tempo, unit and phase; one function tells the engine."""

    def test_establish_grid_clock_has_exactly_one_caller(self) -> None:
        callers = [
            site
            for path in ENGINE_SOURCES
            for site in _calls_named(path, "establish_grid_clock")
        ]
        self.assertEqual(
            callers,
            [f"{GRID_SYNC_SRC.name}:{self._seam_line()}"],
            "the engine's cycle must be set through apply_established_grid, "
            "which pairs the tempo send with the phase mark it performs:\n"
            + "\n".join(callers),
        )

    def _seam_line(self) -> int:
        sites = _calls_named(GRID_SYNC_SRC, "establish_grid_clock")
        self.assertEqual(len(sites), 1, f"expected one seam: {sites}")
        return int(sites[0].split(":")[1])

    def test_no_module_but_sl_grid_sync_writes_the_tempo(self) -> None:
        """Writing the tempo IS resetting the phase, so it is not a `/set` like
        any other. engine.cpp:2174-2178."""
        offenders = [
            site
            for path in ENGINE_SOURCES
            if path.name != GRID_SYNC_SRC.name
            for site in _tempo_writes(path)
        ]
        self.assertEqual(
            offenders,
            [],
            "these zero the engine's downbeat outside the one seam, so the "
            "bench's bar line and the engine's can part company without "
            "anything saying so:\n" + "\n".join(offenders),
        )

    def test_sl_grid_sync_still_writes_it(self) -> None:
        """Proof the check above is not vacuous. Two sites: the startup default
        in `apply_grid_sync`, and the establishment in `establish_grid_clock`."""
        self.assertEqual(len(_tempo_writes(GRID_SYNC_SRC)), 2)

    def test_the_seam_marks_phase_zero_and_sends_the_tempo_last(self) -> None:
        grid = GridState()
        grid.restore(138.0, 4, 6.939)
        sent: list[tuple[str, list]] = []
        apply_established_grid(
            lambda p, a: sent.append((p, list(a))),
            grid,
            num_loops=2,
            now=1234.5,
            arm_loops=False,
        )
        controls = [a[0] for _p, a in sent]
        self.assertEqual(
            controls, ["smart_eighths", "eighth_per_cycle", "tempo"],
            "smart_eighths first or the engine rewrites the cycle under 60 BPM; "
            "tempo last because it is the phase reset (engine.cpp:2174-2178)",
        )
        self.assertEqual(grid.phase_zero_at, 1234.5)

    def test_the_seam_sends_the_bar_count_not_a_default(self) -> None:
        # The real pair for a 6.939 s take: derived, not hand-picked. Fitting a
        # round 138 BPM to it instead is off by 17 ms a cycle, which is the
        # rounding `derive_tempo` refuses for exactly this reason.
        bpm, bars = derive_tempo(6.939)
        self.assertEqual(bars, 4)
        grid = GridState()
        grid.restore(bpm, bars, 6.939)
        sent: list[tuple[str, list]] = []
        apply_established_grid(
            lambda p, a: sent.append((p, list(a))),
            grid, num_loops=2, now=0.0, arm_loops=False,
        )
        eighths = next(a[1] for _p, a in sent if a[0] == "eighth_per_cycle")
        self.assertEqual(eighths, float(EIGHTH_PER_CYCLE * 4))
        # SL's own cycle formula, engine.cpp:2310. The engine and the bench
        # must land on the same number or they quantize to different bars.
        # This is the invariant `d06fb08` introduced, restated at the seam.
        self.assertAlmostEqual(eighths * 30.0 / bpm, grid.cycle_s, places=9)

    def test_the_seam_refuses_when_there_is_no_grid(self) -> None:
        """Sending a tempo with nothing established would zero the engine's
        phase against a bar line nobody has agreed on."""
        with self.assertRaises(ValueError):
            apply_established_grid(
                lambda p, a: None, GridState(), num_loops=2, now=0.0,
                arm_loops=True,
            )

    def test_arm_loops_is_required(self) -> None:
        """Every call site has to say whether it is re-arming the loops or only
        moving the phase. A default would let a phase-only reset quietly
        re-send ~90 messages into live playback, or an establishment quietly
        skip arming them."""
        grid = GridState()
        grid.restore(120.0, 1, 2.0)
        with self.assertRaises(TypeError):
            apply_established_grid(
                lambda p, a: None, grid, num_loops=2, now=0.0
            )


class SongGridRoundTripTests(unittest.TestCase):
    """A song carries its own grid, or it is not a song.

    Before 2026-08-30 the manifest stored `bpm` alone and `load_song` called
    `establish_grid_clock(send, bpm)` — bar count left at its default of 1. A
    song whose first take read as 4 bars at 138 BPM came back with the engine
    quantizing to `8 * 30 / 138 = 1.74 s` against a 6.94 s take.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _probe(self, *, tempo: float, eighths: float | None):
        probe = MagicMock()
        state = {0: SL_STATE_PLAYING}

        def get(ctrl: str, loop: int = 0, timeout: float = 1.5):
            if loop == -1 and ctrl == "tempo":
                return tempo
            if loop == -1 and ctrl == "eighth_per_cycle":
                return eighths
            if ctrl == "state":
                return state.get(loop, 0)
            if ctrl == "loop_len":
                return 6.939 if loop == 0 else 0.0
            if ctrl == "wet":
                return 1.0
            return None

        probe.get.side_effect = get
        probe.send = MagicMock()
        return probe

    def _write_wav(self, probe) -> None:
        def send(path: str, args) -> None:
            if path.endswith("/save_loop"):
                Path(args[0]).write_bytes(b"\0" * 4096)

        probe.send.side_effect = send

    def test_a_four_bar_grid_survives_save_and_load(self) -> None:
        probe = self._probe(tempo=138.0, eighths=32.0)
        self._write_wav(probe)
        result = save_song(probe, "Four Bars", num_loops=2, songs_dir=self.root)
        self.assertTrue(result.ok, result.message)

        raw = json.loads(
            manifest_path("four-bars", songs_dir=self.root).read_text(encoding="utf-8")
        )
        self.assertEqual(raw["bars"], 4)
        self.assertAlmostEqual(raw["cycle_s"], 32.0 * 30.0 / 138.0)

        song = parse_manifest(raw, slug="four-bars")
        self.assertEqual(song.bars, 4)

        loader = self._probe(tempo=120.0, eighths=8.0)
        sent: list[tuple[str, list]] = []
        loader.send.side_effect = lambda p, a: sent.append(
            (p, list(a) if isinstance(a, (list, tuple)) else [a])
        )
        load_song(loader, "four-bars", num_loops=2, songs_dir=self.root)
        eighths = [a[1] for p, a in sent if p == "/set" and a[0] == "eighth_per_cycle"]
        tempos = [a[1] for p, a in sent if p == "/set" and a[0] == "tempo"]
        self.assertIn(float(EIGHTH_PER_CYCLE * 4), eighths,
                      "the song's own unit, not the default one bar")
        self.assertIn(138.0, tempos, "the song's own tempo")

    def test_a_song_saved_before_the_unit_existed_still_loads(self) -> None:
        """Every manifest already on Mitch's appliance. Absent `bars`/`cycle_s`
        it reads one bar — which is exactly what the old code did, so nothing
        he has saved changes behaviour."""
        old = build_manifest_v2(
            name="Old", slug="old", bpm=120.0, grid_active=True,
            tracks=[], saved_at="",
        )
        old.pop("bars")
        old.pop("cycle_s")
        old["loops"] = [{"i": 0, "file": "old_0.wav", "len_s": 2.0, "sl_state": 4}]
        old["version"] = 1
        song = parse_manifest(old, slug="old")
        self.assertEqual(song.bars, 1)
        self.assertAlmostEqual(song.cycle_s, 2.0)

    def test_an_unanswered_engine_falls_back_to_one_bar(self) -> None:
        """`eighth_per_cycle` unanswered is the old behaviour, not a wrong
        cycle: a save that silently invented a unit would be worse than one
        that admits it only knows the tempo."""
        probe = self._probe(tempo=120.0, eighths=None)
        self._write_wav(probe)
        result = save_song(probe, "Quiet Engine", num_loops=2, songs_dir=self.root)
        self.assertTrue(result.ok, result.message)
        raw = json.loads(
            manifest_path("quiet-engine", songs_dir=self.root).read_text(encoding="utf-8")
        )
        self.assertEqual(raw["bars"], 1)
        self.assertAlmostEqual(raw["cycle_s"], 2.0)


class _RecordingOsc:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sent: list[tuple[str, object]] = []

    def send_message(self, path, args) -> None:
        with self._lock:
            self.sent.append((path, args))

    def clear(self) -> None:
        with self._lock:
            self.sent.clear()

    def hits(self, loop: int = 0) -> list[str]:
        with self._lock:
            return [a for p, a in self.sent if p == f"/sl/{loop}/hit"]


if __name__ == "__main__":
    unittest.main()
