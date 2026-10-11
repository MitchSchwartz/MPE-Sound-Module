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

def _build_driver(kind: str, source: str, out: Path) -> None:
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Wno-unused-function",
         f"-I{FAKE_JACK}", f"-DCLIENT_{kind}", f'-DCLIENT_SRC="{REPO / source}"',
         str(FAKE_JACK / "driver.c"), "-o", str(out), "-lm", "-lpthread"],
        check=True, capture_output=True, text=True, timeout=120)


def _feed(g: Graph) -> str:
    lines = [f"port {p}" for p in sorted(g.ports)]
    lines += ["edge " + "\t".join(sorted(e)) for e in g.edges]
    return "\n".join(lines) + "\nend\n"


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

    def test_the_meter_hears_what_the_authority_hears(self):
        for (name, g), line in zip(self.cases, self._run(self.meter)):
            with self.subTest(scenario=name):
                self.assertEqual(line, f"surge_playback={int(ar.live_path_ok(g, TOPO))}")

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

    def test_the_mirrors_name_the_same_clients(self):
        for rel in ("native/mpe-live-monitor/mpe-live-monitor.c",
                    "native/mpe-peak-meter/mpe-peak-meter.c"):
            src = (REPO / rel).read_text()
            self.assertIn(f'#define TRUNK_INSERT "{TRUNK_INSERT}"', src, rel)
            self.assertIn(f'#define SURGE_CLIENT_DEFAULT "{ar.SURGE_CLIENT_DEFAULT}"', src, rel)
        meter = (REPO / "native/mpe-peak-meter/mpe-peak-meter.c").read_text()
        self.assertIn(f'#define MONITOR_INSERT "{MONITOR_INSERT}"', meter)
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
            "taps Surge onto its own input (pre-FX, by design) and mirrors the "
            "live-path predicate for sl-watchdog",
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
