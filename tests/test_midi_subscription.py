"""The check that a MIDI port we opened is actually subscribed.

The bug: rtmidi's open_port() succeeded, the startup banner printed a complete
and correct device line, and no pad press could arrive — for 17 minutes, twice
in one morning, with no error anywhere.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from midi_subscription import port_subscriptions, wait_for_subscription  # noqa: E402

CONNECTED = """\
Client   0 : "System" [Kernel]
  Port   0 : "Timer" (RWeX)
Client  32 : "APC MINI" [Kernel Legacy]
  Port   0 : "APC MINI MIDI 1" (RWeX) [In/Out]
    Connecting To: 164:0
    Connected From: 166:0[r:0]
Client  36 : "LUMI Keys BLOCK" [Kernel Legacy]
  Port   0 : "LUMI" (RWeX)
"""

# The exact failure: the device is present and named, with no subscribers.
DEAD = """\
Client  32 : "APC MINI" [Kernel Legacy]
  Port   0 : "APC MINI MIDI 1" (RWeX) [In/Out]
Client  36 : "LUMI Keys BLOCK" [Kernel Legacy]
  Port   0 : "LUMI" (RWeX)
    Connecting To: 168:0
"""

OUT_ONLY = """\
Client  32 : "APC MINI" [Kernel Legacy]
  Port   0 : "APC MINI MIDI 1" (RWeX) [In/Out]
    Connected From: 166:0[r:0]
"""


class PortSubscriptionTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        import tempfile

        d = Path(tempfile.mkdtemp())
        p = d / "clients"
        p.write_text(text)
        return p

    def test_a_healthy_device_reports_both_directions(self) -> None:
        self.assertEqual(
            port_subscriptions("APC MINI", path=self._write(CONNECTED)), (True, True)
        )

    def test_the_actual_failure_is_detected(self) -> None:
        """Present, correctly named, zero subscribers — what the banner hid."""
        self.assertEqual(
            port_subscriptions("APC MINI", path=self._write(DEAD)), (False, False)
        )

    def test_another_devices_subscription_is_not_credited(self) -> None:
        """LUMI is connected in the DEAD fixture. Attributing its subscription
        to the APC would make the check pass in exactly the broken case."""
        reader, _ = port_subscriptions("APC MINI", path=self._write(DEAD))
        self.assertFalse(reader)
        self.assertTrue(port_subscriptions("LUMI", path=self._write(DEAD))[0])

    def test_leds_without_pads_is_distinguishable(self) -> None:
        """Output-only: LEDs light, no press arrives. Warn, do not fail."""
        self.assertEqual(
            port_subscriptions("APC MINI", path=self._write(OUT_ONLY)), (False, True)
        )

    def test_absent_device_is_not_subscribed(self) -> None:
        self.assertEqual(
            port_subscriptions("NOT PRESENT", path=self._write(CONNECTED)),
            (False, False),
        )

    def test_missing_procfs_does_not_block_startup(self) -> None:
        """On a host with no ALSA procfs the check cannot know, and refusing to
        start on that basis would be worse than the bug it prevents."""
        self.assertEqual(
            port_subscriptions("APC MINI", path=Path("/nonexistent/seq/clients")),
            (True, True),
        )

    def test_wait_returns_as_soon_as_the_reader_appears(self) -> None:
        self.assertEqual(
            wait_for_subscription("APC MINI", timeout_s=0.3, poll_s=0.01,
                                  path=self._write(CONNECTED)),
            (True, True),
        )

    def test_wait_gives_up_and_reports_dead(self) -> None:
        import time

        started = time.monotonic()
        reader, _ = wait_for_subscription("APC MINI", timeout_s=0.2, poll_s=0.05,
                                          path=self._write(DEAD))
        self.assertFalse(reader)
        self.assertGreaterEqual(time.monotonic() - started, 0.2,
                                "it must actually wait — the failure is a race")
