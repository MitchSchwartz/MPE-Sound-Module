"""Pacing and link recovery — the 2026-08-27 USB re-enumeration defect."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

import apc_link  # noqa: E402
from apc_link import LinkHealth, PacedMidiOut  # noqa: E402


class FakeOut:
    def __init__(self, fail_after: int | None = None) -> None:
        self.sent: list[list[int]] = []
        self._fail_after = fail_after

    def send_message(self, msg) -> None:
        if self._fail_after is not None and len(self.sent) >= self._fail_after:
            raise OSError("device gone")
        self.sent.append(list(msg))


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class PacingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out = FakeOut()
        self.clock = Clock()
        self.paced = PacedMidiOut(self.out, gap_s=0.001, now=self.clock)

    def test_a_64_pad_repaint_does_not_burst(self) -> None:
        """The burst is the defect: 64 back-to-back writes into a contended
        full-speed chain stalled the endpoint and dropped the device."""
        for note in range(64):
            self.paced.send_message([0x90, note, 1])
        self.paced.pump()
        self.assertEqual(len(self.out.sent), 1, "one message per gap, no more")
        self.assertEqual(self.paced.backlog, 63)

    def test_the_whole_repaint_still_arrives(self) -> None:
        for note in range(64):
            self.paced.send_message([0x90, note, 1])
        for _ in range(64):
            self.paced.pump()
            self.clock.t += 0.001
        self.assertEqual(len(self.out.sent), 64)
        self.assertEqual(self.paced.backlog, 0)

    def test_order_is_preserved(self) -> None:
        for note in range(5):
            self.paced.send_message([0x90, note, 1])
        for _ in range(5):
            self.paced.pump()
            self.clock.t += 0.001
        self.assertEqual([m[1] for m in self.out.sent], [0, 1, 2, 3, 4])

    def test_pump_never_sleeps_or_blocks(self) -> None:
        """It shares a thread with pad handling. Trading dead pads for late
        ones is not a fix."""
        for note in range(64):
            self.paced.send_message([0x90, note, 1])
        self.paced.pump()  # clock does not advance on its own
        self.paced.pump()
        self.assertEqual(len(self.out.sent), 1)

    def test_a_write_that_raises_drops_the_backlog(self) -> None:
        """Writes raise while the device re-enumerates. The queued messages
        describe a surface that no longer exists."""
        out = FakeOut(fail_after=2)
        paced = PacedMidiOut(out, gap_s=0.0, now=self.clock)
        for note in range(10):
            paced.send_message([0x90, note, 1])
        paced.pump()
        self.assertEqual(len(out.sent), 2)
        self.assertEqual(paced.backlog, 0, "stale surface, not worth replaying")

    def test_reset_points_at_the_reopened_port(self) -> None:
        self.paced.send_message([0x90, 0, 1])
        fresh = FakeOut()
        self.paced.reset(fresh)
        self.paced.send_message([0x90, 9, 1])
        self.paced.pump()
        self.assertEqual(fresh.sent, [[0x90, 9, 1]])
        self.assertEqual(self.out.sent, [], "the stale backlog is discarded")


class LinkHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.logs: list[str] = []
        self.reader = True
        self.reopens = 0
        self._orig = apc_link.port_subscriptions
        apc_link.port_subscriptions = lambda key: (self.reader, True)

    def tearDown(self) -> None:
        apc_link.port_subscriptions = self._orig

    def _health(self, *, reopen_works: bool = True) -> LinkHealth:
        def on_lost() -> bool:
            self.reopens += 1
            if reopen_works:
                self.reader = True
            return reopen_works

        return LinkHealth("APC MINI", on_lost=on_lost, log=self.logs.append,
                          check_s=2.0, now=self.clock)

    def test_a_healthy_link_does_nothing(self) -> None:
        h = self._health()
        for _ in range(5):
            h.poll()
            self.clock.t += 2.0
        self.assertEqual(self.reopens, 0)
        self.assertEqual(self.logs, [])

    def test_a_lost_link_is_detected_and_reopened(self) -> None:
        h = self._health()
        h.poll()
        self.reader = False              # the device re-enumerated
        self.clock.t += 2.0
        h.poll()
        self.assertEqual(self.reopens, 1)
        self.assertTrue(h.healthy)
        self.assertTrue(any("LOST" in m for m in self.logs))
        self.assertTrue(any("reopened" in m for m in self.logs))

    def test_the_loss_is_logged_once_not_every_poll(self) -> None:
        """It ran for hours in this state. The log must say so once, loudly,
        not scroll the same line forever."""
        h = self._health(reopen_works=False)
        self.reader = False
        for _ in range(6):
            h.poll()
            self.clock.t += 2.0
        self.assertEqual(len([m for m in self.logs if "LOST" in m]), 1)
        self.assertEqual(h.losses, 1)

    def test_it_keeps_retrying_until_the_device_returns(self) -> None:
        h = self._health(reopen_works=False)
        self.reader = False
        for _ in range(4):
            h.poll()
            self.clock.t += 2.0
        self.assertEqual(self.reopens, 4, "a device back in 30 s must come back on its own")
        self.assertFalse(h.healthy)

    def test_a_reopen_that_lies_is_not_believed(self) -> None:
        """`open_port` reporting success while subscribing to nothing is the
        original bug. A reopen claiming success is re-verified."""
        h = self._health(reopen_works=False)
        self.reader = False
        h.poll()
        self.clock.t += 2.0

        def on_lost() -> bool:
            return True          # claims success, never restores the reader

        h2 = LinkHealth("APC MINI", on_lost=on_lost, log=self.logs.append,
                        check_s=2.0, now=self.clock)
        h2.poll()
        self.assertFalse(h2.healthy)
        self.assertTrue(any("still no reader" in m for m in self.logs))

    def test_recovery_is_announced(self) -> None:
        h = self._health(reopen_works=False)
        self.reader = False
        h.poll()
        self.clock.t += 2.0
        self.reader = True
        h.poll()
        self.assertTrue(h.healthy)
        self.assertTrue(any("RESTORED" in m for m in self.logs))


if __name__ == "__main__":
    unittest.main()
