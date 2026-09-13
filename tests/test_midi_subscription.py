"""The check that the MIDI port we opened is subscribed BY US.

The first bug: rtmidi's open_port() succeeded, the startup banner printed a
complete and correct device line, and no pad press could arrive — for 17
minutes, twice in one morning, with no error anywhere.

The second (2026-09-13): the check counted any subscriber on any APC port. The
pressure remapper grabbed the APC's Notes port after a USB drop, and the link
was declared RESTORED while the bench held nothing.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from midi_subscription import (  # noqa: E402
    own_client_names,
    port_subscriptions,
    reader_holds_device,
    split_rtmidi_port,
    wait_for_subscription,
)

READER, WRITER = own_client_names(pid=1885294)
MK2_CONTROL = "APC mini mk2:APC mini mk2 Control 36:0"
MINI_MK1 = "APC MINI:APC MINI MIDI 1 32:0"

# Verbatim /proc/asound/seq/clients from the appliance after the 16:20 restart
# (pools trimmed), with the bench's two clients carrying the names this fix
# gives them. The remapper (134) holds the Notes port; the bench holds Control.
HEALTHY = f"""\
Client  16 : "LUMI Keys BLOCK" [Kernel Legacy]
  Port   0 : "LUMI Keys BLOCK MIDI 1" (RWeX) [In/Out]
    Connecting To: 132:0
Client  36 : "APC mini mk2" [Kernel Legacy]
  Port   0 : "APC mini mk2 Control" (RWeX) [In/Out]
    Connecting To: 137:0
    Connected From: 138:0[r:0]
  Port   1 : "APC mini mk2 Notes" (RWeX) [In/Out]
    Connecting To: 134:0
Client 132 : "RtMidiIn Client" [User Legacy]
  Port   0 : "RtMidi input" (-We-) [Out]
    Connected From: 16:0
Client 134 : "RtMidiIn Client" [User Legacy]
  Port   0 : "RtMidi input" (-We-) [Out]
    Connected From: 36:1
Client 137 : "{READER}" [User Legacy]
  Port   0 : "RtMidi input" (-We-) [Out]
    Connected From: 36:0
Client 138 : "{WRITER}" [User Legacy]
  Port   0 : "RtMidi output" (R-e-) [In]
    Connecting To: 36:0[r:0]
"""

# 2026-09-13 16:20, before the restart: the APC re-enumerated as client 44,
# the remapper subscribed to Notes, and the bench's clients are connected to
# nothing. The old check called this "pads live again".
REMAPPER_HOLDS_IT = f"""\
Client  44 : "APC mini mk2" [Kernel Legacy]
  Port   0 : "APC mini mk2 Control" (RWeX) [In/Out]
  Port   1 : "APC mini mk2 Notes" (RWeX) [In/Out]
    Connecting To: 134:0
Client 134 : "RtMidiIn Client" [User Legacy]
  Port   0 : "RtMidi input" (-We-) [Out]
    Connected From: 44:1
Client 137 : "{READER}" [User Legacy]
  Port   0 : "RtMidi input" (-We-) [Out]
Client 138 : "{WRITER}" [User Legacy]
  Port   0 : "RtMidi output" (R-e-) [In]
"""

# Someone else on the SAME port we opened, still not us.
STRANGER_ON_OUR_PORT = """\
Client  36 : "APC mini mk2" [Kernel Legacy]
  Port   0 : "APC mini mk2 Control" (RWeX) [In/Out]
    Connecting To: 134:0
    Connected From: 129:0[r:0]
Client 129 : "RtMidiOut Client" [User Legacy]
  Port   0 : "RtMidi output" (R-e-) [In]
Client 134 : "RtMidiIn Client" [User Legacy]
  Port   0 : "RtMidi input" (-We-) [Out]
"""

# The 2026-08-27 restart race: the dying bench (previous pid) is subscribed,
# the new one is not.
PREVIOUS_BENCH = """\
Client  32 : "APC MINI" [Kernel Legacy]
  Port   0 : "APC MINI MIDI 1" (RWeX) [In/Out]
    Connecting To: 164:0
    Connected From: 166:0[r:0]
Client 164 : "mpe-looper-apc-in-2254" [User Legacy]
  Port   0 : "RtMidi input" (-We-) [Out]
Client 166 : "mpe-looper-apc-out-2254" [User Legacy]
  Port   0 : "RtMidi output" (R-e-) [In]
"""


def _write(text: str) -> Path:
    p = Path(tempfile.mkdtemp()) / "clients"
    p.write_text(text)
    return p


def _subs(port: str, text: str) -> tuple[bool, bool]:
    return port_subscriptions(port, reader=READER, writer=WRITER, path=_write(text))


class PortSubscriptionTests(unittest.TestCase):
    def test_our_own_subscription_reports_both_directions(self) -> None:
        self.assertEqual(_subs(MK2_CONTROL, HEALTHY), (True, True))

    def test_the_remapper_holding_the_apc_is_not_us(self) -> None:
        """The 2026-09-13 failure, verbatim. A reader exists on the device —
        the remapper, on Notes — and the bench holds nothing."""
        self.assertEqual(_subs(MK2_CONTROL, REMAPPER_HOLDS_IT), (False, False))

    def test_a_stranger_on_the_very_port_we_opened_is_not_credited(self) -> None:
        self.assertEqual(_subs(MK2_CONTROL, STRANGER_ON_OUR_PORT), (False, False))

    def test_a_previous_bench_process_is_not_credited(self) -> None:
        """Same name stem, different pid: the dying instance of the 08-27 race."""
        self.assertEqual(_subs(MINI_MK1, PREVIOUS_BENCH), (False, False))
        self.assertEqual(
            port_subscriptions(MINI_MK1, reader="mpe-looper-apc-in-2254",
                               writer="mpe-looper-apc-out-2254",
                               path=_write(PREVIOUS_BENCH)),
            (True, True),
        )

    def test_the_port_is_matched_by_name_whatever_the_client_number(self) -> None:
        """The APC was client 32, 44 and 36 in one afternoon; rtmidi's label
        carries whichever number it had when we opened it."""
        stale_label = "APC mini mk2:APC mini mk2 Control 44:0"
        self.assertEqual(_subs(stale_label, HEALTHY), (True, True))

    def test_our_subscription_on_the_other_port_does_not_count(self) -> None:
        notes = "APC mini mk2:APC mini mk2 Notes 36:1"
        self.assertEqual(_subs(notes, HEALTHY), (False, False))

    def test_absent_device_is_not_subscribed(self) -> None:
        self.assertEqual(_subs("NOT PRESENT:Nothing 9:0", HEALTHY), (False, False))

    def test_missing_procfs_does_not_block_startup(self) -> None:
        """On a host with no ALSA procfs the check cannot know, and refusing to
        start on that basis would be worse than the bug it prevents."""
        self.assertEqual(
            port_subscriptions(MK2_CONTROL, reader=READER, writer=WRITER,
                               path=Path("/nonexistent/seq/clients")),
            (True, True),
        )

    def test_rtmidi_labels_split_into_names(self) -> None:
        self.assertEqual(split_rtmidi_port(MK2_CONTROL),
                         ("APC mini mk2", "APC mini mk2 Control"))
        self.assertEqual(split_rtmidi_port(MINI_MK1), ("APC MINI", "APC MINI MIDI 1"))

    def test_client_names_carry_the_pid(self) -> None:
        self.assertEqual(own_client_names(pid=7),
                         ("mpe-looper-apc-in-7", "mpe-looper-apc-out-7"))

    def test_wait_returns_as_soon_as_our_reader_appears(self) -> None:
        self.assertEqual(
            wait_for_subscription(MK2_CONTROL, reader=READER, writer=WRITER,
                                  timeout_s=0.3, poll_s=0.01, path=_write(HEALTHY)),
            (True, True),
        )

    def test_wait_gives_up_and_reports_dead(self) -> None:
        import time

        started = time.monotonic()
        reader, _ = wait_for_subscription(
            MK2_CONTROL, reader=READER, writer=WRITER, timeout_s=0.2, poll_s=0.05,
            path=_write(REMAPPER_HOLDS_IT),
        )
        self.assertFalse(reader)
        self.assertGreaterEqual(time.monotonic() - started, 0.2,
                                "it must actually wait — the failure is a race")


class RestartScriptCheckTests(unittest.TestCase):
    """`restart-looper-session.sh` asks by pid. It used to ask with awk whether
    anything read from any APC port, and printed PASS for the remapper."""

    SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper" / "midi_subscription.py"

    def _cli(self, text: str, pid: int) -> int:
        import os
        import subprocess

        env = dict(os.environ, MPE_SEQ_CLIENTS=str(_write(text)))
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), "--pid", str(pid), "--device", "APC"],
            capture_output=True, text=True, env=env, timeout=30,
        ).returncode

    def test_the_session_holding_the_apc_passes(self) -> None:
        self.assertEqual(self._cli(HEALTHY, 1885294), 0)

    def test_the_remapper_holding_the_apc_fails(self) -> None:
        """The deploy printed 'PASS — APC has ALSA reader' for exactly this."""
        self.assertEqual(self._cli(REMAPPER_HOLDS_IT, 1885294), 1)

    def test_a_previous_session_does_not_pass_for_the_new_one(self) -> None:
        self.assertEqual(self._cli(PREVIOUS_BENCH, 1885294), 1)
        self.assertEqual(self._cli(PREVIOUS_BENCH, 2254), 0)

    def test_an_unreadable_graph_is_not_a_pass(self) -> None:
        self.assertFalse(
            reader_holds_device("APC", reader=READER, path=Path("/nonexistent/clients"))
        )

    def test_the_shell_has_no_parser_of_its_own(self) -> None:
        sh = (Path(__file__).resolve().parents[1] / "scripts"
              / "restart-looper-session.sh").read_text()
        self.assertNotIn("Connecting To", sh,
                         "a second parser of the seq graph is how 'any reader' came back")
        self.assertIn("midi_subscription.py", sh)
        self.assertIn("MainPID", sh)


if __name__ == "__main__":
    unittest.main()
