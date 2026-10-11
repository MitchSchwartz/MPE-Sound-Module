#!/usr/bin/env python3
"""WHERE Surge's audio goes. One topology, one set of answers.

**If a Surge -> playback or Surge -> loop-input connection is decided anywhere
else, that is the bug.** `tests/test_audio_routing.py` refuses a second opinion.

WHY THIS FILE EXISTS

Until 2026-10-11 the correct graph was written out by hand in five places that
could not see each other: `wire-jack-graph.sh`, `restore-direct-monitor-path.sh`,
`wire-sooperlooper-graph.sh` / `restart-sooperlooper.sh` (their record-path
checks), and the two native clients. Each knew exactly one insert,
`mpe-live-monitor`, by name. A second insert -- the FX host, on the trunk --
would have needed a sixth copy, and any copy that still connected
`Surge XT:out_N -> system:playback_N` underneath it would sum the dry and
processed signals: louder, comb-filtered, and silent in every log.

THE TOPOLOGY

    Surge XT:out_N ─> [mpe-fx-host:in_N ─ out_N] ─> fan-out ─┬─> [mpe-live-monitor:in_N ─ out_N] ─> system:playback_N
                                                              └─> mpe-looper:loopM_in_N

Both inserts are optional. The FX host sits on the TRUNK, before the fan-out,
so loops record what you hear. The live monitor sits on the monitor branch
only, so it can never reach the capture path.

THE RULES

  * **The fan-out source.** `mpe-fx-host:out_N` when the FX host is carrying,
    otherwise `Surge XT:out_N`. "Carrying" means its input is fed by Surge on
    EVERY channel: one channel wet and the other dry is an L/R fault, not a
    partial success. This is the fail-open rule -- an FX host that is dead,
    restarting, or orphaned by a Surge restart is simply not the source, and
    everything falls back to Surge directly.
  * **The monitor insert carries** when its input is fed by the fan-out source
    and its output reaches playback, on every channel. Both legs, exact names:
    an insert wired to playback with nothing feeding it is the silent
    instrument this rule was first written to catch.
  * **Exactly one path** from Surge to each destination. Two -- direct and
    through an insert at once -- is two copies of your playing.

WHO DOES WHAT

The wirers and repairers only ever ADD an edge, and only to a destination that
nothing from Surge reaches at all (`plan`). They never disconnect: taking the
direct path away is the job of the insert that replaces it, done once it is
carrying audio and given back when it leaves (`mpe-live-monitor.c` is the
model). That split is what keeps every repair here fail-open, and what means a
repair can never create the doubled path -- it does not add to a destination
that is already reached, by any route.

The native clients cannot import this. They carry a mirror of the two rules
above (`fanout_source` / `surge_playback_wired`), and
`tests/test_audio_routing.py::NativeMirrorTests` compiles them against a fake
JACK and checks they agree with this file on every graph it can think of.

CLI (for the shell scripts; reads `jack_lsp -c` on stdin, no JACK client here):

    audio_routing.py plan  [--loops N] [--scope fanout|monitor]   edges to add, "SRC<TAB>DST"
    audio_routing.py check record [--loops N]                     0 ok, 1 broken, 2 no looper
    audio_routing.py source                                       the fan-out source per channel
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

# --- names -------------------------------------------------------------------

#: Stereo, and everything downstream assumes it. Raising this alone wires
#: channels nothing else knows about (see the C clients' CHANNELS).
CHANNELS: tuple[int, ...] = (1, 2)

SURGE_CLIENT_DEFAULT = "Surge XT"
LOOPER_CLIENT_DEFAULT = "mpe-looper"
#: The trunk insert. Absent today; harmless when absent.
TRUNK_INSERT = "mpe-fx-host"
#: The monitor-branch insert.
MONITOR_INSERT = "mpe-live-monitor"
#: 15 usable max -- see scripts/sooperlooper/sl_limits.py.
LOOPS_DEFAULT = 15


def playback(ch: int) -> str:
    return f"system:playback_{ch}"


@dataclass(frozen=True)
class Topology:
    """The names the graph is built from. Env is read once, here."""

    surge: str = SURGE_CLIENT_DEFAULT
    looper: str = LOOPER_CLIENT_DEFAULT
    loops: int = LOOPS_DEFAULT

    @classmethod
    def from_env(cls, *, loops: int | None = None) -> "Topology":
        surge = os.environ.get("MPE_SL_SURGE_CLIENT") or SURGE_CLIENT_DEFAULT
        looper = os.environ.get("MPE_SL_JACK_CLIENT") or LOOPER_CLIENT_DEFAULT
        if loops is None:
            try:
                loops = int(os.environ.get("MPE_SL_LOOPS") or LOOPS_DEFAULT)
            except ValueError:
                loops = LOOPS_DEFAULT
        return cls(surge=surge, looper=looper, loops=max(0, loops))

    def surge_out(self, ch: int) -> str:
        return f"{self.surge}:out_{ch}"

    def loop_in(self, loop: int, ch: int) -> str:
        return f"{self.looper}:loop{loop}_in_{ch}"


def insert_in(client: str, ch: int) -> str:
    return f"{client}:in_{ch}"


def insert_out(client: str, ch: int) -> str:
    return f"{client}:out_{ch}"


# --- the graph as `jack_lsp -c` shows it --------------------------------------

@dataclass
class Graph:
    """Ports, and connections as unordered pairs.

    `jack_lsp -c` lists each connection under both of its ports with no
    direction. Every question asked here names an output and an input
    explicitly, so direction is never needed -- the same reason the shell
    versions could `grep` one port's listing for the other.
    """

    ports: set[str] = field(default_factory=set)
    edges: set[frozenset[str]] = field(default_factory=set)

    @classmethod
    def parse(cls, text: str) -> "Graph":
        graph = cls()
        current: str | None = None
        for raw in text.splitlines():
            if not raw.strip():
                continue
            if raw[0] in " \t":
                # Connection lines are indented; property / latency lines
                # (`jack_lsp -p`, `-l`) are tab-indented and carry a colon-space
                # label. Port names never start with whitespace.
                line = raw.strip()
                if current is None or line.startswith(("properties:", "port latency",
                                                        "total latency")):
                    continue
                graph.connect(current, line)
                continue
            current = raw.rstrip()
            graph.ports.add(current)
        return graph

    def connect(self, a: str, b: str) -> None:
        self.ports.update((a, b))
        self.edges.add(frozenset((a, b)))

    def connected(self, a: str, b: str) -> bool:
        return frozenset((a, b)) in self.edges

    def has(self, port: str) -> bool:
        return port in self.ports

    def client_present(self, client: str) -> bool:
        prefix = client + ":"
        return any(p.startswith(prefix) for p in self.ports)


# --- the rules ---------------------------------------------------------------

def trunk_insert_carrying(graph: Graph, topo: Topology) -> bool:
    """Is the FX host on the trunk? Its input fed by Surge, every channel."""
    return all(graph.connected(topo.surge_out(ch), insert_in(TRUNK_INSERT, ch))
               for ch in CHANNELS)


def fanout_source(graph: Graph, topo: Topology, ch: int) -> str:
    """The port that feeds the fan-out on channel `ch`. Fail-open to Surge."""
    if trunk_insert_carrying(graph, topo):
        return insert_out(TRUNK_INSERT, ch)
    return topo.surge_out(ch)


def monitor_insert_carrying(graph: Graph, topo: Topology) -> bool:
    """Does the live monitor carry the live signal to playback? Both legs."""
    return all(
        graph.connected(fanout_source(graph, topo, ch), insert_in(MONITOR_INSERT, ch))
        and graph.connected(insert_out(MONITOR_INSERT, ch), playback(ch))
        for ch in CHANNELS
    )


def surge_paths(graph: Graph, topo: Topology, dest: str, ch: int) -> int:
    """How many routes carry Surge's channel `ch` into `dest`.

    Counts real signal paths, not presence: a route through an insert counts
    only if that insert's own input is reached. An insert's in_N -> out_N is
    the only internal edge assumed; the looper is a sink here (its loops are
    set to dry=0 precisely so that it is not a pass-through).
    """
    memo: dict[str, int] = {}

    def into(port: str, depth: int) -> int:
        # Paths from Surge's out_ch into `port`.
        if depth > 6:                       # pragma: no cover - the graph is 3 deep
            return 0
        if port in memo:
            return memo[port]
        total = 0
        for feeder in (topo.surge_out(ch), insert_out(TRUNK_INSERT, ch),
                       insert_out(MONITOR_INSERT, ch)):
            if not graph.connected(feeder, port):
                continue
            if feeder == topo.surge_out(ch):
                total += 1
            else:
                client = feeder.split(":", 1)[0]
                if insert_in(client, ch) != port:
                    total += into(insert_in(client, ch), depth + 1)
        memo[port] = total
        return total

    return into(dest, 0)


def live_path_ok(graph: Graph, topo: Topology) -> bool:
    """Can you hear yourself play? The C meter's `surge_playback=` mirrors this.

    Playback is fed by the fan-out source directly, or by the monitor insert
    while the fan-out source feeds it -- on every channel.
    """
    for ch in CHANNELS:
        source = fanout_source(graph, topo, ch)
        direct = graph.connected(source, playback(ch))
        through = (graph.connected(insert_out(MONITOR_INSERT, ch), playback(ch))
                   and graph.connected(source, insert_in(MONITOR_INSERT, ch)))
        if not (direct or through):
            return False
    return True


def record_path_ok(graph: Graph, topo: Topology) -> bool | None:
    """Does Surge reach every loop input? None when no looper is on the graph.

    "Reach", not "is wired from the fan-out source": a loop input fed by Surge
    directly while the FX host has not yet taken it over still records, and
    a repair that reported it broken would spin -- `plan` will not add a second
    path to a destination that already has one.
    """
    if not graph.client_present(topo.looper):
        return None
    return all(surge_paths(graph, topo, topo.loop_in(loop, ch), ch) >= 1
               for loop in range(topo.loops) for ch in CHANNELS)


def destinations(graph: Graph, topo: Topology, *, scope: str = "fanout") -> list[tuple[int, str]]:
    """Every port the fan-out owns, as (channel, port).

    Loop inputs only when the looper is on the graph -- otherwise this is
    thirty connects to ports that do not exist. Playback only when the monitor
    insert is not carrying: when it is, playback is ITS output's to feed, and
    its input is its own to connect.
    """
    out: list[tuple[int, str]] = []
    if scope not in ("fanout", "monitor"):
        raise ValueError(f"unknown scope {scope!r}")
    # An empty graph means `jack_lsp` failed, not that JACK is empty -- system
    # ports always exist. Unknown is answered fail-open: every insert is treated
    # as absent and every destination as wanted, which is what the shell
    # versions did when their own probes failed.
    unknown = not graph.ports
    if scope == "fanout" and (unknown or graph.client_present(topo.looper)):
        out.extend((ch, topo.loop_in(loop, ch))
                   for loop in range(topo.loops) for ch in CHANNELS)
    if not monitor_insert_carrying(graph, topo):
        out.extend((ch, playback(ch)) for ch in CHANNELS)
    return out


def plan(graph: Graph, topo: Topology, *, scope: str = "fanout") -> list[tuple[str, str]]:
    """Edges to ADD so every destination is reached. Never one that doubles.

    A destination is skipped when anything from Surge already reaches it, by
    any route -- including a stale direct edge the FX host has not removed
    yet. Adding the fan-out source beside it would be the doubled-signal fault;
    leaving it is the insert's business, and it removes it on its next pass.
    """
    edges: list[tuple[str, str]] = []
    for ch, dest in destinations(graph, topo, scope=scope):
        if surge_paths(graph, topo, dest, ch) == 0:
            edges.append((fanout_source(graph, topo, ch), dest))
    return edges


def doubled(graph: Graph, topo: Topology) -> list[str]:
    """Destinations reached by more than one path. Should always be empty."""
    found: list[str] = []
    candidates = [(ch, playback(ch)) for ch in CHANNELS]
    candidates += [(ch, topo.loop_in(loop, ch))
                   for loop in range(topo.loops) for ch in CHANNELS]
    for ch, dest in candidates:
        if surge_paths(graph, topo, dest, ch) > 1:
            found.append(dest)
    return found


def _assert_well_formed() -> None:
    """Names that would make the rules above mean something else."""
    names = {SURGE_CLIENT_DEFAULT, LOOPER_CLIENT_DEFAULT, TRUNK_INSERT, MONITOR_INSERT}
    if len(names) != 4:
        raise ValueError("two roles share one JACK client name")
    if tuple(sorted(CHANNELS)) != (1, 2):
        raise ValueError("the native clients and the restore paths are stereo only")


_assert_well_formed()


# --- CLI ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JACK routing authority (reads `jack_lsp -c` on stdin)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan", help="edges to add, one 'SRC<TAB>DST' per line")
    p_plan.add_argument("--loops", type=int, default=None)
    p_plan.add_argument("--scope", choices=("fanout", "monitor"), default="fanout")
    p_plan.add_argument("--report-monitor", action="store_true",
                        help="end with '#monitor_carrying=0|1' (for the restore script)")
    p_check = sub.add_parser("check", help="exit 0 ok, 1 broken, 2 not applicable")
    p_check.add_argument("what", choices=("record", "live", "doubled"))
    p_check.add_argument("--loops", type=int, default=None)
    sub.add_parser("source", help="the fan-out source port per channel")
    args = ap.parse_args(argv)

    topo = Topology.from_env(loops=getattr(args, "loops", None))
    graph = Graph.parse(sys.stdin.read())

    if args.cmd == "plan":
        for src, dst in plan(graph, topo, scope=args.scope):
            print(f"{src}\t{dst}")
        if args.report_monitor:
            print(f"#monitor_carrying={int(monitor_insert_carrying(graph, topo))}")
        return 0
    if args.cmd == "source":
        for ch in CHANNELS:
            print(fanout_source(graph, topo, ch))
        return 0
    if args.what == "record":
        ok = record_path_ok(graph, topo)
        if ok is None:
            print(f"record path: {topo.looper} is not on the graph")
            return 2
        print(f"record path: {'ok' if ok else 'BROKEN'} "
              f"(source {fanout_source(graph, topo, 1)}, loops 0..{topo.loops - 1})")
        return 0 if ok else 1
    if args.what == "live":
        ok = live_path_ok(graph, topo)
        print(f"live path: {'ok' if ok else 'BROKEN'}")
        return 0 if ok else 1
    found = doubled(graph, topo)
    for dest in found:
        print(f"doubled: {dest}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
