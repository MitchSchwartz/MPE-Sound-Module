"""Watchdog graph reasoning.

These cover the failure the watchdog missed for 45 minutes on 2026-08-15:
SooperLooper survived a jackd restart as a process but lost its JACK client.
`/get` kept answering while `/set` and `/hit` went into a queue nothing
drained, so every read-only check said "healthy" and the JACK repair kept
connecting a port that did not exist.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_PATH = (Path(__file__).resolve().parents[1]
         / "scripts" / "sooperlooper" / "sl-watchdog.py")
_spec = importlib.util.spec_from_file_location("sl_watchdog", _PATH)
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)


HEALTHY = """system:playback_1
   mpe-looper:common_out_1
system:playback_2
   mpe-looper:common_out_2
mpe-looper:loop0_in_1
   Surge XT:out_1
"""

# What the Pi actually looked like at 20:20 on 2026-08-15: jackd back up,
# Surge re-registered, SooperLooper simply absent from the graph.
ORPHANED = """system:playback_1
   Surge XT:out_1
system:playback_2
   Surge XT:out_2
Surge XT:out_1
   system:playback_1
"""


class JackClientVisibleTests(unittest.TestCase):
    def test_sees_the_client_when_its_ports_are_on_the_graph(self) -> None:
        self.assertTrue(watchdog.jack_client_visible(HEALTHY))

    def test_reports_orphan_when_the_client_has_no_ports(self) -> None:
        self.assertFalse(watchdog.jack_client_visible(ORPHANED))

    def test_only_port_owner_lines_count(self) -> None:
        """Indented lines are the far end of someone else's connection.

        `jack_lsp -c` prints owned ports flush left and their peers indented.
        A stale graph can still name mpe-looper as a peer; counting that as
        presence would call an orphan healthy.
        """
        self.assertFalse(watchdog.jack_client_visible(
            "system:playback_1\n   mpe-looper:common_out_1\n"
            .replace("   mpe-looper", "\tmpe-looper")))

    def test_empty_graph_is_not_visible(self) -> None:
        self.assertFalse(watchdog.jack_client_visible(""))


class PlaybackSourcesTests(unittest.TestCase):
    def test_collects_what_feeds_the_speakers(self) -> None:
        self.assertEqual(
            watchdog.playback_sources(HEALTHY),
            {"mpe-looper:common_out_1", "mpe-looper:common_out_2"},
        )

    def test_empty_graph_yields_no_sources(self) -> None:
        """An empty set means "nothing reaches the speakers", not "unknown".

        The watchdog used to guard its audio-path check with `if srcs and ...`,
        so a completely disconnected graph — the worst audio failure it exists
        to catch — was reported as healthy.
        """
        self.assertEqual(watchdog.playback_sources(""), set())


if __name__ == "__main__":
    unittest.main()
