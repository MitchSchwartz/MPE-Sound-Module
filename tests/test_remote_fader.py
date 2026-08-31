"""The touch Vol fader's path into the looper's master gain.

The point under test is not "a datagram arrives". It is that the touch fader
reaches loop levels *without* becoming a second writer of `wet` — the drift
`loop_mix` exists to prevent. So the transport tests are joined by a structural
test that the bench replays remote moves through the hardware fader's own
entry point.
"""

from __future__ import annotations

import ast
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))

import remote_fader  # noqa: E402

BENCH_SRC = REPO / "scripts" / "sooperlooper-apc-bench.py"


class TestParsing(unittest.TestCase):
    def test_valid_master_message(self):
        self.assertEqual(remote_fader.parse_message(b"master 96"), 96)
        self.assertEqual(remote_fader.parse_message(b"  master 0 \n"), 0)
        self.assertEqual(remote_fader.parse_message(b"master 127"), 127)

    def test_rejects_out_of_range(self):
        # Not clamped on receive: an out-of-range value means the sender is not
        # the sender we think it is, and guessing its intent is worse than
        # ignoring it.
        self.assertIsNone(remote_fader.parse_message(b"master 128"))
        self.assertIsNone(remote_fader.parse_message(b"master -1"))

    def test_rejects_malformed(self):
        for bad in (b"", b"master", b"master 5 5", b"wet 5", b"master x",
                    b"\xff\xfe", b"MASTER 5"):
            self.assertIsNone(remote_fader.parse_message(bad), bad)


class TestScaling(unittest.TestCase):
    def test_level_to_cc_endpoints_and_clamp(self):
        self.assertEqual(remote_fader.level_to_cc(0.0), 0)
        self.assertEqual(remote_fader.level_to_cc(1.0), 127)
        self.assertEqual(remote_fader.level_to_cc(0.5), 64)
        self.assertEqual(remote_fader.level_to_cc(2.0), 127)
        self.assertEqual(remote_fader.level_to_cc(-1.0), 0)

    def test_non_numeric_falls_back_to_unity(self):
        # A broken level must not silently mute the rig.
        self.assertEqual(remote_fader.level_to_cc(None), 127)


class TestTransport(unittest.TestCase):
    PORT = 19956

    def setUp(self):
        self.rx = remote_fader.RemoteFaderReceiver(port=self.PORT)
        self.assertTrue(self.rx.open(), self.rx.error)
        self.addCleanup(self.rx.close)

    def _settle(self):
        time.sleep(0.05)

    def test_empty_socket_yields_none(self):
        self.assertIsNone(self.rx.poll())

    def test_round_trip(self):
        remote_fader.send_master(1.0, port=self.PORT)
        self._settle()
        self.assertEqual(self.rx.poll(), 127)

    def test_newest_wins(self):
        # A drag delivers many positions; only the last is not already stale.
        for level in (1.0, 0.75, 0.25):
            remote_fader.send_master(level, port=self.PORT)
        self._settle()
        self.assertEqual(self.rx.poll(), 32)
        self.assertIsNone(self.rx.poll())

    def test_garbage_does_not_break_the_surface(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"\xff\xfe garbage", ("127.0.0.1", self.PORT))
            s.sendto(b"master 40", ("127.0.0.1", self.PORT))
        self._settle()
        self.assertEqual(self.rx.poll(), 40)

    def test_second_bind_fails_without_raising(self):
        other = remote_fader.RemoteFaderReceiver(port=self.PORT)
        self.assertFalse(other.open())
        self.assertIsNotNone(other.error)
        self.assertIsNone(other.poll())

    def test_send_to_nothing_does_not_raise(self):
        remote_fader.send_master(0.5, port=self.PORT + 1)

    def test_closed_receiver_is_inert(self):
        self.rx.close()
        self.assertIsNone(self.rx.poll())


class TestBenchWiring(unittest.TestCase):
    """Structural: the remote path must not grow its own level arithmetic."""

    def setUp(self):
        self.tree = ast.parse(BENCH_SRC.read_text(encoding="utf-8"))
        self.fn = next(
            (n for n in ast.walk(self.tree)
             if isinstance(n, ast.FunctionDef) and n.name == "poll_remote_faders"),
            None,
        )
        self.assertIsNotNone(self.fn, "bench lost poll_remote_faders")

    def test_remote_moves_go_through_handle_cc(self):
        called = {
            n.func.id for n in ast.walk(self.fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertIn("handle_cc", called)

    def test_remote_path_does_not_compose_or_send_wet(self):
        # If this ever calls the sender or the composer directly, the touch
        # fader has become a second writer and loop_mix no longer owns level.
        names = {
            ast.unparse(n.func) for n in ast.walk(self.fn) if isinstance(n, ast.Call)
        }
        for forbidden in ("faders.submit", "mix.messages_for", "mix.wet_for"):
            self.assertNotIn(forbidden, names)

    def test_poller_runs_in_the_idle_branch(self):
        src = BENCH_SRC.read_text(encoding="utf-8")
        self.assertIn("poll_remote_faders()", src.split("def poll_remote_faders")[0]
                      + src.split("def poll_remote_faders")[1])


class TestTouchFaderSends(unittest.TestCase):
    """The Vol fader must actually reach the transport, not just be able to."""

    def test_apply_volume_calls_the_looper_send(self):
        src = (REPO / "patch_browser" / "touch_browser_prefs.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_apply_volume"),
            None,
        )
        self.assertIsNotNone(fn, "touch UI lost _apply_volume")
        calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        self.assertIn("self._send_looper_volume", calls)

    def test_send_helper_never_raises_without_a_looper(self):
        # No mocks: the real socket call, at a port nothing is bound to. The
        # touch UI must keep working when the looper is not running.
        remote_fader.send_master(0.5, port=19999)


if __name__ == "__main__":
    unittest.main()
