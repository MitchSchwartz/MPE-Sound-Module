"""`audio_routing` — the one place that decides where Surge's audio goes.

Three kinds of test, and the last two are the ones that matter.

The first checks the answers: given a graph, does the authority name the right
fan-out source, the right edges to add, the right health verdicts?

The second checks that the native clients — which cannot import Python — give
the SAME answers. They are compiled against a fake JACK (tests/fixtures/fake_jack)
and run over every graph the scenario generator can build. A mirror that is
only pinned by reading its source agrees with the authority until the day
someone edits one of them.

The third checks that **nothing else answers the question**: no file outside
the authority hard-codes a Surge -> playback or Surge -> loop-input connection.
Five files used to, each knowing one insert by name. An FX host on the trunk
would have made a sixth, and any one of them reconnecting Surge beneath it
doubles the live signal.
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from patch_browser import audio_routing as ar
from patch_browser.audio_routing import (
    MONITOR_INSERT,
    TRUNK_INSERT,
    Graph,
    Topology,
)

REPO = Path(__file__).resolve().parents[1]
FAKE_JACK = REPO / "tests" / "fixtures" / "fake_jack"

TOPO = Topology(loops=2)
S = TOPO.surge_out
FX_IN = lambda ch: ar.insert_in(TRUNK_INSERT, ch)     # noqa: E731
FX_OUT = lambda ch: ar.insert_out(TRUNK_INSERT, ch)   # noqa: E731
MON_IN = lambda ch: ar.insert_in(MONITOR_INSERT, ch)  # noqa: E731
MON_OUT = lambda ch: ar.insert_out(MONITOR_INSERT, ch)  # noqa: E731
PB = ar.playback


def graph(*edges: tuple[str, str], ports: tuple[str, ...] = ()) -> Graph:
    g = Graph()
    g.ports.update(("system:playback_1", "system:playback_2", S(1), S(2)))
    g.ports.update(ports)
    for a, b in edges:
        g.connect(a, b)
    return g


def both(fn) -> list[tuple[str, str]]:
    return [fn(ch) for ch in ar.CHANNELS]


LOOPER_PORTS = tuple(TOPO.loop_in(m, ch) for m in range(TOPO.loops) for ch in ar.CHANNELS) + (
    "mpe-looper:common_out_1", "mpe-looper:common_out_2")
TODAY = both(lambda ch: (S(ch), PB(ch)))  # the graph on dev: Surge straight to playback


class Answers(unittest.TestCase):
    def test_today_the_source_is_surge(self):
        g = graph(*TODAY)
        self.assertEqual([ar.fanout_source(g, TOPO, ch) for ch in ar.CHANNELS], [S(1), S(2)])
        self.assertTrue(ar.live_path_ok(g, TOPO))

    def test_a_fresh_looper_gets_surge_on_every_input_and_playback_is_left_alone(self):
        g = graph(*TODAY, ports=LOOPER_PORTS)
        self.assertEqual(
            sorted(ar.plan(g, TOPO)),
            sorted((S(ch), TOPO.loop_in(m, ch)) for m in range(2) for ch in ar.CHANNELS))
        self.assertIs(ar.record_path_ok(g, TOPO), False)

    def test_a_carrying_monitor_owns_playback(self):
        g = graph(*both(lambda ch: (S(ch), MON_IN(ch))), *both(lambda ch: (MON_OUT(ch), PB(ch))))
        self.assertTrue(ar.monitor_insert_carrying(g, TOPO))
        self.assertTrue(ar.live_path_ok(g, TOPO))
        self.assertEqual(ar.plan(g, TOPO, scope="monitor"), [])

    def test_a_monitor_with_nothing_feeding_it_gets_the_direct_path_back(self):
        """The silent-instrument case `live_monitor_carrying` was written for."""
        g = graph(*both(lambda ch: (MON_OUT(ch), PB(ch))), ports=(MON_IN(1), MON_IN(2)))
        self.assertFalse(ar.monitor_insert_carrying(g, TOPO))
        self.assertFalse(ar.live_path_ok(g, TOPO))
        self.assertEqual(ar.plan(g, TOPO, scope="monitor"), both(lambda ch: (S(ch), PB(ch))))

    def test_an_fx_host_on_the_trunk_becomes_the_source(self):
        g = graph(*both(lambda ch: (S(ch), FX_IN(ch))), ports=LOOPER_PORTS + (FX_OUT(1), FX_OUT(2)))
        self.assertTrue(ar.trunk_insert_carrying(g, TOPO))
        self.assertEqual([ar.fanout_source(g, TOPO, ch) for ch in ar.CHANNELS], [FX_OUT(1), FX_OUT(2)])
        added = ar.plan(g, TOPO)
        self.assertIn((FX_OUT(1), PB(1)), added)
        self.assertIn((FX_OUT(2), TOPO.loop_in(1, 2)), added)
        self.assertFalse(any(src.startswith(TOPO.surge + ":") for src, _ in added))

    def test_one_fed_channel_is_not_carrying(self):
        g = graph((S(1), FX_IN(1)), ports=(FX_IN(2), FX_OUT(1), FX_OUT(2)))
        self.assertFalse(ar.trunk_insert_carrying(g, TOPO))
        self.assertEqual(ar.fanout_source(g, TOPO, 1), S(1))

    def test_fail_open_an_fx_host_that_lost_its_input_is_not_the_source(self):
        """After a Surge restart the FX host is up, its outputs still wired,
        and silent. The source is Surge, and the destinations count as unfed."""
        g = graph(*both(lambda ch: (FX_OUT(ch), PB(ch))), ports=(FX_IN(1), FX_IN(2)) + LOOPER_PORTS)
        self.assertEqual(ar.fanout_source(g, TOPO, 1), S(1))
        self.assertFalse(ar.live_path_ok(g, TOPO))
        self.assertIn((S(1), PB(1)), ar.plan(g, TOPO))

    def test_plan_never_adds_beside_a_route_that_already_reaches(self):
        """FX carrying, but Surge still directly on a loop input (the FX host
        has not moved it yet). Adding fx:out there would be two copies."""
        g = graph(*both(lambda ch: (S(ch), FX_IN(ch))), (S(1), TOPO.loop_in(0, 1)),
                  ports=LOOPER_PORTS + (FX_OUT(1), FX_OUT(2)))
        self.assertNotIn(TOPO.loop_in(0, 1), [d for _, d in ar.plan(g, TOPO)])
        self.assertIs(ar.record_path_ok(g, TOPO), False)  # the other inputs are unfed

    def test_doubled_is_seen_through_an_insert(self):
        g = graph(*TODAY, *both(lambda ch: (S(ch), MON_IN(ch))), *both(lambda ch: (MON_OUT(ch), PB(ch))))
        self.assertEqual(sorted(ar.doubled(g, TOPO)), [PB(1), PB(2)])

    def test_an_unknown_graph_is_answered_fail_open(self):
        """`jack_lsp` failed: treat every insert as absent and wire everything."""
        added = ar.plan(Graph(), TOPO)
        self.assertIn((S(1), PB(1)), added)
        self.assertIn((S(2), TOPO.loop_in(1, 2)), added)

    def test_no_looper_no_record_verdict(self):
        self.assertIsNone(ar.record_path_ok(graph(*TODAY), TOPO))

    def test_parses_jack_lsp_listing_with_properties(self):
        text = ("Surge XT:out_1\n   system:playback_1\n\tproperties: output,\n"
                "system:playback_1\n   Surge XT:out_1\n\tproperties: input,physical,\n")
        g = Graph.parse(text)
        self.assertTrue(g.connected("system:playback_1", "Surge XT:out_1"))
        self.assertEqual(g.ports, {"Surge XT:out_1", "system:playback_1"})

    def test_cli_round_trip(self):
        listing = "".join(f"{a}\n   {b}\n{b}\n   {a}\n" for a, b in TODAY)
        listing += "".join(f"{p}\n" for p in LOOPER_PORTS)
        run = lambda *args: subprocess.run(  # noqa: E731
            ["python3", str(REPO / "patch_browser/audio_routing.py"), *args],
            input=listing, capture_output=True, text=True, timeout=30,
            env=dict(os.environ, MPE_SL_LOOPS="2"))
        plan = run("plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(len(plan.stdout.splitlines()), 4)
        self.assertEqual(run("check", "record").returncode, 1)
        self.assertEqual(run("check", "live").returncode, 0)


# --- every graph we can think of ---------------------------------------------

FX_MODES = ("absent", "unfed", "ch1", "carrying")
OPTIONAL = {
    "fx->pb": lambda ch: (FX_OUT(ch), PB(ch)),
    "surge->pb": lambda ch: (S(ch), PB(ch)),
    "surge->mon": lambda ch: (S(ch), MON_IN(ch)),
    "fx->mon": lambda ch: (FX_OUT(ch), MON_IN(ch)),
    "mon->pb": lambda ch: (MON_OUT(ch), PB(ch)),
}


def scenarios():
    for mode in FX_MODES:
        for flags in itertools.product((0, 1), repeat=len(OPTIONAL)):
            edges: list[tuple[str, str]] = []
            ports: list[str] = [MON_IN(1), MON_IN(2), MON_OUT(1), MON_OUT(2)]
            if mode != "absent":
                ports += [FX_IN(1), FX_IN(2), FX_OUT(1), FX_OUT(2)]
            if mode == "ch1":
                edges.append((S(1), FX_IN(1)))
            if mode == "carrying":
                edges += both(lambda ch: (S(ch), FX_IN(ch)))
            for on, (name, fn) in zip(flags, OPTIONAL.items()):
                if on and not (mode == "absent" and name.startswith("fx")):
                    edges += both(fn)
            yield f"{mode}:{''.join(map(str, flags))}", graph(*edges, ports=tuple(ports))


def fx_scenarios():
    """Graphs the FX host can wake up to. Its own ports always exist (it is the
    process under test); Surge, the looper and the monitor may or may not."""
    fx_edges = {
        "none": [],
        "ch1": [(S(1), FX_IN(1))],
        "both": both(lambda ch: (S(ch), FX_IN(ch))),
    }
    optional = {
        "surge->pb": lambda ch: (S(ch), PB(ch)),
        "fx->pb": lambda ch: (FX_OUT(ch), PB(ch)),
        "surge->mon": lambda ch: (S(ch), MON_IN(ch)),
        "mon->pb": lambda ch: (MON_OUT(ch), PB(ch)),
        "surge->loop0": lambda ch: (S(ch), TOPO.loop_in(0, ch)),
        "fx->loop1": lambda ch: (FX_OUT(ch), TOPO.loop_in(1, ch)),
    }
    for fx_mode, base in fx_edges.items():
        for monitor in (False, True):
            for looper in (False, True):
                for flags in itertools.product((0, 1), repeat=len(optional)):
                    ports = [FX_IN(1), FX_IN(2), FX_OUT(1), FX_OUT(2)]
                    if monitor:
                        ports += [MON_IN(1), MON_IN(2), MON_OUT(1), MON_OUT(2)]
                    if looper:
                        ports += list(LOOPER_PORTS)
                    edges = list(base)
                    for on, (key, fn) in zip(flags, optional.items()):
                        needs_mon = "mon" in key
                        needs_loop = "loop" in key
                        if on and (monitor or not needs_mon) and (looper or not needs_loop):
                            edges += both(fn)
                    yield (f"{fx_mode}:mon{int(monitor)}:loop{int(looper)}:"
                           f"{''.join(map(str, flags))}", graph(*edges, ports=tuple(ports)))
    # Surge gone (restarting): the FX host must touch nothing.
    gone = Graph()
    gone.ports.update(("system:playback_1", "system:playback_2", FX_IN(1), FX_IN(2),
                       FX_OUT(1), FX_OUT(2)) + LOOPER_PORTS)
    for ch in ar.CHANNELS:
        gone.connect(FX_OUT(ch), PB(ch))
    yield "surge-gone", gone


class PlanProperties(unittest.TestCase):
    def test_applying_the_plan_reaches_everything_and_doubles_nothing(self):
        for name, g in scenarios():
            with self.subTest(scenario=name):
                before = ar.doubled(g, TOPO)
                for src, dst in ar.plan(g, TOPO, scope="monitor"):
                    g.connect(src, dst)
                for ch in ar.CHANNELS:
                    self.assertGreaterEqual(ar.surge_paths(g, TOPO, PB(ch), ch), 1)
                self.assertEqual(ar.doubled(g, TOPO), before,
                                 "the plan added a second path somewhere")


# --- the native mirrors -------------------------------------------------------

def _build_driver(kind: str, source: str, out: Path, extra: tuple[str, ...] = ()) -> None:
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Wno-unused-function",
         f"-I{FAKE_JACK}", f"-I{(REPO / source).parent}", f"-DCLIENT_{kind}",
         f'-DCLIENT_SRC="{REPO / source}"',
         str(FAKE_JACK / "driver.c"), *(str(REPO / e) for e in extra),
         "-o", str(out), "-lm", "-lpthread"],
        check=True, capture_output=True, text=True, timeout=120)


def _feed(g: Graph, levels: dict[str, float] | None = None) -> str:
    lines = [f"port {p}" for p in sorted(g.ports)]
    lines += ["edge " + "\t".join(sorted(e)) for e in g.edges]
    lines += [f"level {port}\t{value}" for port, value in sorted((levels or {}).items())
              if port in g.ports]
    return "\n".join(lines) + "\nend\n"


METER_IN = lambda ch: f"mpe-peak-meter:in_{ch}"  # noqa: E731
#: What each trunk port plays in the meter scenarios. Surge is hot but legal;
#: the FX host's output sits at its own +-1.0 clamp, as a phaser's added peaks
#: leave it on a chord Surge plays at -2 dBFS.
SURGE_LEVEL = 0.8
FX_LEVEL = 1.0
#: CLIP_HOLD_WRITES in mpe-peak-meter.c: clip= stays up this many writes.
CLIP_HOLD_WRITES = 8


def meter_scenarios():
    """Every routing scenario, with the meter's tap left in each state a
    previous pass, an FX host arriving, or one leaving could leave it in."""
    presets = {
        "tap-none": lambda ch: [],
        "tap-surge": lambda ch: [(S(ch), METER_IN(ch))],
        "tap-fx": lambda ch: [(FX_OUT(ch), METER_IN(ch))],
        "tap-both": lambda ch: [(S(ch), METER_IN(ch)), (FX_OUT(ch), METER_IN(ch))],
    }
    for name, g in scenarios():
        for preset, fn in presets.items():
            out = Graph()
            out.ports.update(g.ports)
            out.edges.update(g.edges)
            for ch in ar.CHANNELS:
                for a, b in fn(ch):
                    if out.has(a):
                        out.connect(a, b)
            yield f"{name}:{preset}", out
    # Surge restarting: its ports are gone, the FX host is idle.
    gone = Graph()
    gone.ports.update(("system:playback_1", "system:playback_2",
                       FX_IN(1), FX_IN(2), FX_OUT(1), FX_OUT(2)))
    yield "surge-gone", gone


@unittest.skipUnless(shutil.which("cc"), "no C compiler — native mirrors not checked")
class NativeMirrorTests(unittest.TestCase):
    """The C clients decide with the authority's rules, proven by running them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.monitor = cls.tmp / "monitor"
        cls.meter = cls.tmp / "meter"
        _build_driver("MONITOR", "native/mpe-live-monitor/mpe-live-monitor.c", cls.monitor)
        _build_driver("METER", "native/mpe-peak-meter/mpe-peak-meter.c", cls.meter)
        cls.fx = cls.tmp / "fx"
        _build_driver("FX", "native/mpe-fx-host/mpe-fx-host.c", cls.fx,
                      extra=("native/mpe-fx-host/fx_dsp.c",))
        cls.cases = list(scenarios())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, binary: Path) -> list[str]:
        proc = subprocess.run([str(binary)], input="".join(_feed(g) for _, g in self.cases),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), len(self.cases), "driver skipped scenarios")
        return lines

    def _run_meter(self):
        cases = list(meter_scenarios())
        levels = {S(ch): SURGE_LEVEL for ch in ar.CHANNELS}
        levels.update({FX_OUT(ch): FX_LEVEL for ch in ar.CHANNELS})
        proc = subprocess.run([str(self.meter)],
                              input="".join(_feed(g, levels) for _, g in cases),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), len(cases), "driver skipped scenarios")
        for (name, g), line in zip(cases, lines):
            yield name, g, dict(f.split("=", 1) for f in line.split("\t"))

    def test_the_meter_hears_what_the_authority_hears(self):
        """surge_playback= (sl-watchdog's live-path sensor) is the authority's
        live_path_ok, whatever the meter's own tap is doing."""
        for name, g, fields in self._run_meter():
            with self.subTest(scenario=name):
                self.assertEqual(fields["surge_playback"], str(int(ar.live_path_ok(g, TOPO))))

    def test_wired_still_means_surge_is_on_the_graph(self):
        """wired= is audio-engine.sh's on-graph probe. It answers for Surge's
        ports, not for where the meter listens: an FX host carrying the trunk
        does not change it, and Surge gone does."""
        for name, g, fields in self._run_meter():
            with self.subTest(scenario=name):
                present = all(g.has(S(ch)) for ch in ar.CHANNELS)
                self.assertEqual(fields["wired"], str(int(present)))

    def test_the_meter_taps_the_fanout_source_and_nothing_else(self):
        """The level shown is what leaves the box: the FX host's output while
        it carries, Surge otherwise -- one of them, never both summed."""
        for name, g, fields in self._run_meter():
            with self.subTest(scenario=name):
                taps = fields["tap"].split("|")
                for ch in ar.CHANNELS:
                    feeds = set(filter(None, taps[ch - 1].split("+")))
                    source = ar.fanout_source(g, TOPO, ch)
                    expected = {source} if g.has(source) else set()
                    self.assertEqual(feeds, expected, f"meter in_{ch}")

    def test_the_level_and_the_clip_latch_are_post_fx(self):
        """A clamped FX output at 1.0 over a Surge signal at 0.8: the meter
        reads 1.0 and latches a clip while the FX host carries, and reads 0.8
        with no clip when Surge is the source."""
        held = "1" * CLIP_HOLD_WRITES + "00"
        for name, g, fields in self._run_meter():
            with self.subTest(scenario=name):
                source = ar.fanout_source(g, TOPO, 1)
                if not g.has(source):
                    self.assertEqual(float(fields["peak"]), 0.0)
                    self.assertEqual(fields["clip"], "0" * len(held))
                elif ar.trunk_insert_carrying(g, TOPO):
                    self.assertAlmostEqual(float(fields["peak"]), FX_LEVEL, places=4)
                    self.assertEqual(fields["clip"], held)
                else:
                    self.assertAlmostEqual(float(fields["peak"]), SURGE_LEVEL, places=4)
                    self.assertEqual(fields["clip"], "0" * len(held))

    def test_the_monitor_follows_the_authority(self):
        for (name, g), line in zip(self.cases, self._run(self.monitor)):
            with self.subTest(scenario=name):
                fields = dict(f.split("=", 1) for f in line.split("\t", 3))
                source = [ar.fanout_source(g, TOPO, ch) for ch in ar.CHANNELS]
                self.assertEqual(fields["source"].split("|"), source)
                self.assertEqual(fields["carrying_before"], str(int(ar.monitor_insert_carrying(g, TOPO))))

                after = Graph()
                after.ports.update(g.ports)
                for pair in filter(None, fields["edges"].split("|")):
                    after.connect(*pair.split("\t"))
                self.assertEqual(fields["carrying_after"],
                                 str(int(ar.monitor_insert_carrying(after, TOPO))))
                for ch in ar.CHANNELS:
                    # Its input is fed by the source and by no other trunk port.
                    self.assertTrue(after.connected(source[ch - 1], MON_IN(ch)))
                    other = FX_OUT(ch) if source[ch - 1] == S(ch) else S(ch)
                    self.assertFalse(after.connected(other, MON_IN(ch)))
                    if fields["carrying_after"] == "1":
                        self.assertFalse(after.connected(S(ch), PB(ch)), "Surge left on playback")
                        self.assertFalse(after.connected(FX_OUT(ch), PB(ch)), "FX left on playback")
                        self.assertEqual(ar.surge_paths(after, TOPO, PB(ch), ch), 1)

    def test_the_fx_host_takes_and_returns_the_trunk_as_the_authority_says(self):
        """The FX host's whole wiring life, on every graph: one pass, then exit.

        After the pass it is the fan-out source and every destination is fed
        from it exactly once; Surge feeds none of them directly; the authority
        has nothing left to add. After `leave_trunk` (its SIGTERM path) it is
        not the source, feeds nothing, and Surge is back everywhere.
        """
        cases = list(fx_scenarios())
        proc = subprocess.run([str(self.fx)], input="".join(_feed(g) for _, g in cases),
                              capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, MPE_SL_LOOPS=str(TOPO.loops)))
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), len(cases), "driver skipped scenarios")

        def edges(field: str, ports: set[str]) -> Graph:
            out = Graph()
            out.ports.update(ports)
            for pair in filter(None, field.split("|")):
                out.connect(*pair.split(">"))
            return out

        for (name, g), line in zip(cases, lines):
            with self.subTest(scenario=name):
                fields = dict(f.split("=", 1) for f in line.split("\t"))
                self.assertEqual(fields["source"].split("|"),
                                 [ar.fanout_source(g, TOPO, ch) for ch in ar.CHANNELS])
                self.assertEqual(fields["carrying_before"], str(int(ar.trunk_insert_carrying(g, TOPO))))
                after = edges(fields["edges"], g.ports)
                back = edges(fields["returned"], g.ports)
                surge_present = g.has(S(1)) and g.has(S(2))
                self.assertEqual(fields["carrying_after"],
                                 str(int(ar.trunk_insert_carrying(after, TOPO))))
                if not surge_present:
                    # Nothing to carry and nothing to give back to: hands off.
                    self.assertEqual(fields["carrying_after"], "0")
                    self.assertEqual(after.edges, g.edges)
                    self.assertEqual(back.edges, g.edges)
                    continue

                self.assertEqual(fields["carrying_after"], "1")
                loops = [(ch, TOPO.loop_in(m, ch)) for m in range(TOPO.loops)
                         for ch in ar.CHANNELS] if g.client_present(TOPO.looper) else []
                for ch, dest in loops:
                    self.assertTrue(after.connected(FX_OUT(ch), dest), dest)
                    self.assertFalse(after.connected(S(ch), dest), f"Surge left on {dest}")
                    self.assertEqual(ar.surge_paths(after, TOPO, dest, ch), 1, dest)
                for ch in ar.CHANNELS:
                    self.assertFalse(after.connected(S(ch), PB(ch)), "Surge left on playback")
                    if g.has(MON_IN(ch)):
                        self.assertTrue(after.connected(FX_OUT(ch), MON_IN(ch)))
                        self.assertFalse(after.connected(S(ch), MON_IN(ch)))
                self.assertTrue(ar.live_path_ok(after, TOPO))
                self.assertEqual(ar.plan(after, TOPO), [], "the authority still wants edges")
                for dest in ar.doubled(after, TOPO):
                    # Only the monitor's own clean-up may be pending: a direct
                    # FX -> playback edge that was ALREADY there, beneath a
                    # monitor that now carries. One the FX host made itself
                    # would be a doubled signal until the monitor's next pass.
                    ch = int(dest[-1])
                    self.assertEqual(dest, PB(ch))
                    self.assertTrue(ar.monitor_insert_carrying(after, TOPO))
                    self.assertTrue(g.connected(FX_OUT(ch), PB(ch)),
                                    "the FX host doubled playback under a carrying monitor")

                self.assertFalse(ar.trunk_insert_carrying(back, TOPO))
                for ch in ar.CHANNELS:
                    self.assertFalse(any(FX_OUT(ch) in e for e in back.edges), "FX still feeds")
                for ch, dest in loops:
                    self.assertTrue(back.connected(S(ch), dest), dest)
                self.assertTrue(ar.live_path_ok(back, TOPO))
                self.assertEqual(ar.plan(back, TOPO), [])

    def test_the_mirrors_name_the_same_clients(self):
        for rel in ("native/mpe-live-monitor/mpe-live-monitor.c",
                    "native/mpe-peak-meter/mpe-peak-meter.c"):
            src = (REPO / rel).read_text()
            self.assertIn(f'#define TRUNK_INSERT "{TRUNK_INSERT}"', src, rel)
            self.assertIn(f'#define SURGE_CLIENT_DEFAULT "{ar.SURGE_CLIENT_DEFAULT}"', src, rel)
        meter = (REPO / "native/mpe-peak-meter/mpe-peak-meter.c").read_text()
        self.assertIn(f'#define MONITOR_INSERT "{MONITOR_INSERT}"', meter)
        fx = (REPO / "native/mpe-fx-host/mpe-fx-host.c").read_text()
        self.assertIn(f'#define CLIENT_NAME "{TRUNK_INSERT}"', fx)
        self.assertIn(f'#define MONITOR_INSERT "{MONITOR_INSERT}"', fx)
        self.assertIn(f'#define SURGE_CLIENT_DEFAULT "{ar.SURGE_CLIENT_DEFAULT}"', fx)
        self.assertIn(f'#define LOOPER_CLIENT_DEFAULT "{ar.LOOPER_CLIENT_DEFAULT}"', fx)
        self.assertIn(f"#define LOOPS_DEFAULT {ar.LOOPS_DEFAULT}", fx)
        monitor = (REPO / "native/mpe-live-monitor/mpe-live-monitor.c").read_text()
        self.assertIn(f'#define CLIENT_NAME "{MONITOR_INSERT}"', monitor)


# --- the shell callers, run against fake JACK tools ----------------------------

def _listing(g: Graph) -> str:
    out = []
    for port in sorted(g.ports):
        out.append(port)
        for e in g.edges:
            if port in e:
                other = next(iter(e - {port}))
                out.append(f"   {other}")
    return "\n".join(out) + "\n"


class ShellCallers(unittest.TestCase):
    """wire-jack-graph.sh and the restore script, end to end with fake tools."""

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = self.dir / "connects"
        self.listing = self.dir / "listing"
        for tool, body in (
            ("jack_lsp", f'cat "{self.listing}"'),
            ("jack_connect", f'printf "%s\\t%s\\n" "$1" "$2" >> "{self.log}"'),
            ("jack_disconnect", f'printf "DISCONNECT %s\\t%s\\n" "$1" "$2" >> "{self.log}"'),
            ("oscsend", "exit 0"),
            ("pgrep", "exit 0"),
        ):
            (self.dir / tool).write_text(f"#!/bin/sh\n{body}\n")
            (self.dir / tool).chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.dir}{os.pathsep}{os.environ['PATH']}",
                        MPE_SL_LOOPS="2", MPE_RUN_DIR=str(self.dir), MPE_USB_STEM_CHANNELS="2")

    def _connects(self) -> set[tuple[str, str]]:
        if not self.log.exists():
            return set()
        return {tuple(l.split("\t")) for l in self.log.read_text().splitlines()}

    def test_wiring_today_is_unchanged(self):
        """No inserts: Surge onto every loop input; playback left as it is."""
        self.listing.write_text(_listing(graph(*TODAY, ports=LOOPER_PORTS)))
        proc = subprocess.run(["bash", str(REPO / "scripts/sooperlooper/wire-jack-graph.sh"), "connect"],
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        expected = {(S(ch), TOPO.loop_in(m, ch)) for m in range(2) for ch in ar.CHANNELS}
        expected |= {(f"mpe-looper:common_out_{ch}", PB(ch)) for ch in ar.CHANNELS}
        self.assertEqual(self._connects(), expected)

    def test_wiring_restores_surge_to_playback_when_nothing_reaches_it(self):
        self.listing.write_text(_listing(graph(ports=LOOPER_PORTS)))
        subprocess.run(["bash", str(REPO / "scripts/sooperlooper/wire-jack-graph.sh"), "connect"],
                       env=self.env, capture_output=True, text=True, timeout=60)
        self.assertTrue({(S(1), PB(1)), (S(2), PB(2))} <= self._connects())

    def test_wiring_leaves_a_carrying_monitor_alone(self):
        g = graph(*both(lambda ch: (S(ch), MON_IN(ch))), *both(lambda ch: (MON_OUT(ch), PB(ch))),
                  ports=LOOPER_PORTS)
        self.listing.write_text(_listing(g))
        subprocess.run(["bash", str(REPO / "scripts/sooperlooper/wire-jack-graph.sh"), "connect"],
                       env=self.env, capture_output=True, text=True, timeout=60)
        self.assertFalse({c for c in self._connects() if c[1].startswith("system:playback")
                          and not c[0].startswith("mpe-looper:")})

    def test_restore_follows_the_trunk(self):
        """The monitor died while the FX host carries: playback gets the FX host."""
        g = graph(*both(lambda ch: (S(ch), FX_IN(ch))), ports=(FX_OUT(1), FX_OUT(2)))
        self.listing.write_text(_listing(g))
        proc = subprocess.run(["bash", str(REPO / "scripts/restore-direct-monitor-path.sh")],
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self._connects(), {(FX_OUT(1), PB(1)), (FX_OUT(2), PB(2))})
        self.assertIn("detached=0", (self.dir / "live-monitor.state").read_text())

    def test_restore_does_not_speak_for_a_carrying_monitor(self):
        g = graph(*both(lambda ch: (S(ch), MON_IN(ch))), *both(lambda ch: (MON_OUT(ch), PB(ch))))
        self.listing.write_text(_listing(g))
        state = self.dir / "live-monitor.state"
        state.write_text("carrying=1\ndetached=1\nupdated=5\n")
        subprocess.run(["bash", str(REPO / "scripts/restore-direct-monitor-path.sh")],
                       env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(self._connects(), set())
        self.assertIn("detached=1", state.read_text())

    def test_a_surge_restart_rewires_the_record_path_once(self):
        """surge-watchdog's reconcile: acts once per surge.state `started=`."""
        surge_state = self.dir / "surge.state"
        surge_state.write_text("started=100\nactive=jack\n")
        self.listing.write_text(_listing(graph(*TODAY, ports=LOOPER_PORTS)))
        script = (
            f'source "{REPO}/scripts/lib/audio-engine.sh"\n'
            'mpe_reconcile_record_path_after_surge_start test; echo "rc1=$?"\n'
            f': > "{self.log}"\n'
            'mpe_reconcile_record_path_after_surge_start test; echo "rc2=$?"\n'
        )
        env = dict(self.env, MPE_SURGE_STATE_FILE=str(surge_state))
        proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120)
        self.assertIn("rc1=0", proc.stdout, proc.stderr)
        self.assertIn("rc2=0", proc.stdout, proc.stderr)
        self.assertEqual(self._connects(), set(), "the same Surge start was handled twice")

        # A new start, with the record path broken: rewired.
        surge_state.write_text("started=200\nactive=jack\n")
        script = (
            f'source "{REPO}/scripts/lib/audio-engine.sh"\n'
            'mpe_reconcile_record_path_after_surge_start test; echo "rc=$?"\n'
        )
        proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120)
        self.assertIn("rc=0", proc.stdout, proc.stderr)
        self.assertIn((S(1), TOPO.loop_in(1, 1)), self._connects())

    def test_the_watchdog_asks_on_the_on_graph_branch(self):
        text = (REPO / "scripts/surge-watchdog.sh").read_text()
        branch = text[text.index("if mpe_surge_on_jack_graph; then"):]
        branch = branch[:branch.index("return 0")]
        self.assertIn("mpe_reconcile_record_path_after_surge_start", branch)


# --- nothing else decides -------------------------------------------------------

SURGE_OUT = re.compile(r"(?i)surge(?: xt|_client|[a-z_]*)\}?:out")
DESTINATION = re.compile(r"system:playback|playback_|_in_[0-9$%{]|loop\S*_in")
VERB = re.compile(r"jack_connect|jack_disconnect|jack_lsp|\.connect\(|\.disconnect\(|\bgrep\b")
C_CONNECT = re.compile(r"\bjack_(?:dis)?connect\s*\(")


def opinions(path: str, text: str) -> list[str]:
    """Lines in `text` that hard-code a Surge -> playback / loop-input edge.

    Shell and Python: one logical line naming a Surge output port, a playback
    or loop-input port, and a JACK verb (connect, disconnect, or a `jack_lsp` /
    `grep` check). Comments are skipped, so a diagram in a docstring is not an
    opinion -- it has no verb.

    C: a file that calls jack_connect / jack_disconnect and names both Surge's
    client and playback or a loop input. C builds port names on one line and
    connects on another, so there is no honest per-line test.

    Known escapes, written down rather than rediscovered: a port name built in
    a variable on one line and connected on another (shell / Python), and an
    edge reached through a helper defined in another file.
    """
    if path.endswith((".c", ".h")):
        if C_CONNECT.search(text) and re.search(r"surge", text, re.I) and DESTINATION.search(text):
            return [f"{path}: connects Surge-side ports and names playback / loop inputs"]
        return []
    found = []
    logical = text.replace("\\\n", " ")
    for n, line in enumerate(logical.splitlines(), 1):
        if line.lstrip().startswith(("#", "//")):
            continue
        if SURGE_OUT.search(line) and DESTINATION.search(line) and VERB.search(line):
            found.append(f"{path}:{n}: {line.strip()[:120]}")
    return found


class OneAuthority(unittest.TestCase):
    """Nothing outside audio_routing.py decides where Surge's output goes."""

    #: Files that must make these connections at run time and cannot import the
    #: authority. Each mirrors its rules and is executed against it by
    #: NativeMirrorTests; that test, not this list, is what keeps them honest.
    C_MIRRORS: dict[str, str] = {
        "native/mpe-live-monitor/mpe-live-monitor.c":
            "the monitor-branch insert: follows the fan-out source onto its own "
            "input and takes the direct path to playback away while it carries",
        "native/mpe-peak-meter/mpe-peak-meter.c":
            "taps the fan-out source onto its own input (post-FX: what leaves "
            "the box) and mirrors the live-path predicate for sl-watchdog",
        "native/mpe-fx-host/mpe-fx-host.c":
            "the trunk insert: once Surge feeds its input on every channel it "
            "becomes the fan-out source, moves every destination off Surge, and "
            "moves them back when it leaves",
    }
    AUTHORITY = "patch_browser/audio_routing.py"
    ROOTS = ("scripts", "patch_browser", "native", "config")
    SUFFIXES = (".sh", ".py", ".c", ".h", ".service")

    def _sources(self):
        for root in self.ROOTS:
            for path in sorted((REPO / root).rglob("*")):
                if path.is_file() and path.suffix in self.SUFFIXES:
                    yield path.relative_to(REPO).as_posix(), path

    def test_no_second_opinion(self):
        offences = []
        for rel, path in self._sources():
            if rel == self.AUTHORITY or rel in self.C_MIRRORS:
                continue
            offences += opinions(rel, path.read_text(errors="replace"))
        self.assertEqual(offences, [], "\n".join(
            ["a Surge -> playback / loop-input edge decided outside the routing authority:"]
            + offences))

    def test_the_scan_reaches_the_files_it_guards(self):
        """A guard over the wrong scope reads the same as a clean tree."""
        seen = {rel for rel, _ in self._sources()}
        for rel in ("scripts/sooperlooper/wire-jack-graph.sh", "scripts/restore-direct-monitor-path.sh",
                    "scripts/sooperlooper/wire-sooperlooper-graph.sh",
                    "scripts/sooperlooper/restart-sooperlooper.sh", "scripts/lib/audio-engine.sh",
                    "scripts/sooperlooper/sl-watchdog.py", *self.C_MIRRORS):
            self.assertIn(rel, seen)

    def test_every_mirror_is_still_a_file(self):
        for rel in self.C_MIRRORS:
            self.assertTrue((REPO / rel).exists(), f"allowlisted {rel} is gone; drop its entry")

    def test_the_detector_sees_each_shape_of_the_bug(self):
        """Positive controls: the guard must fire on what it guards against."""
        planted = {
            "a.sh": 'jack_connect "${SURGE_CLIENT}:out_1" "system:playback_1"',
            "b.sh": 'jack_connect "Surge XT:out_2" \\\n    "mpe-looper:loop3_in_2"',
            "c.sh": 'jack_lsp -c "${JACK_CLIENT}:loop0_in_1" | grep -Fq "Surge XT:out_1"',
            "d.py": 'client.connect("Surge XT:out_1", "system:playback_1")',
            "e.py": 'subprocess.run(["jack_connect", f"{surge}:out_{ch}", f"mpe-looper:loop{m}_in_{ch}"])',
            "f.c": 'snprintf(a, 9, "%s:out_%d", g_surge_client, 1);\n'
                   'jack_connect(c, a, "system:playback_1");',
        }
        for name, text in planted.items():
            with self.subTest(planted=name):
                self.assertTrue(opinions(name, text), f"{name} slipped past the guard")

    def test_the_detector_leaves_taps_and_prose_alone(self):
        """Negative controls: a meter tap and a diagram are not routing."""
        clean = {
            "tap.sh": 'jack_connect "Surge XT:out_1" "mpe-peak-meter:in_1"',
            "doc.py": '    Surge XT:out_1 -> system:playback_1        (the speakers)',
            "comment.sh": '# jack_connect "Surge XT:out_1" "system:playback_1"',
            "probe.py": 'ap.add_argument("--source-port", default="Surge XT:out_1")',
        }
        for name, text in clean.items():
            with self.subTest(clean=name):
                self.assertEqual(opinions(name, text), [])

    def test_the_callers_ask(self):
        for rel in ("scripts/sooperlooper/wire-jack-graph.sh",
                    "scripts/restore-direct-monitor-path.sh",
                    "scripts/sooperlooper/wire-sooperlooper-graph.sh",
                    "scripts/sooperlooper/restart-sooperlooper.sh",
                    "scripts/lib/audio-engine.sh"):
            self.assertIn("patch_browser/audio_routing.py", (REPO / rel).read_text(), rel)


if __name__ == "__main__":
    unittest.main()
