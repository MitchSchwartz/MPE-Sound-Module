"""Phase 3M — merged looper session unit and entry point."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
LOOPER_SESSION = REPO / "scripts" / "sooperlooper" / "looper_session.py"
INSTALL_UNITS = REPO / "scripts" / "install-units.sh"


def _disabled_units() -> list[str]:
    """Entries in install-units.sh DISABLED.

    Strip comments BEFORE finding the closing paren. Splitting the raw text on the
    first ")" truncates at any parenthesis inside a comment — on 2026-08-19 a commit
    hash in a caveat comment silently cut the list short, so every entry after it
    stopped being checked and the tests still passed.
    """
    text = INSTALL_UNITS.read_text(encoding="utf-8")
    body = text.split("DISABLED=(", 1)[1]
    names = []
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith(")"):
            break
        if line:
            names.append(line)
    return names


class LooperSessionPhase3MTests(unittest.TestCase):
    def test_merged_unit_replaces_bench_and_hud_units(self) -> None:
        self.assertTrue((REPO / "config/mpe-looper-session.service").is_file())
        self.assertFalse((REPO / "config/mpe-apc-bench.service").exists())
        self.assertFalse((REPO / "config/sl-hud-monitor.service").exists())

    def test_install_units_installs_looper_session_but_does_not_enable_it(self) -> None:
        """Phase 3M merged the units; 2026-08-18 made the stack opt-in.

        The merge target must still be installed and must still be the only looper
        client unit — the retired mpe-apc-bench and sl-hud-monitor must not come back.
        It must NOT be in ENABLED: measured xrun cost, see tests/test_systemd_units.py
        and Documents/DECISIONS.md 2026-08-18.
        """
        install = INSTALL_UNITS.read_text(encoding="utf-8")
        enabled = install.split("ENABLED=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("mpe-looper-session", enabled)
        self.assertNotIn("mpe-apc-bench", enabled)
        self.assertNotIn("sl-hud-monitor", enabled)
        self.assertTrue((REPO / "config" / "mpe-looper-session.service").is_file())

    def test_retired_units_in_disabled_block(self) -> None:
        disabled = _disabled_units()
        self.assertIn("mpe-apc-bench", disabled)
        self.assertIn("sl-hud-monitor", disabled)

    def test_install_units_stops_retired_looper_clients_on_upgrade(self) -> None:
        text = INSTALL_UNITS.read_text(encoding="utf-8")
        self.assertIn("RETIRED_LOOPER_CLIENTS=(mpe-apc-bench sl-hud-monitor)", text)
        self.assertIn('systemctl stop --now "$u.service"', text)

    def test_config_drift_sentinel_removed(self) -> None:
        grid_sync = (REPO / "scripts/sooperlooper/sl_grid_sync.py").read_text(
            encoding="utf-8"
        )
        listener = (REPO / "scripts/sooperlooper/sl_bench_listener.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("RESTART_SENTINEL", grid_sync)
        self.assertNotIn("ENGINE_CONFIG_PROBE", grid_sync)
        self.assertNotIn("GLOBAL_CONFIG_PROBE", listener)
        self.assertIn("looper.engine.started", (REPO / "patch_browser/session_events.py").read_text())

    def test_looper_session_entry_exists(self) -> None:
        entry = REPO / "scripts/looper-session.py"
        self.assertTrue(entry.is_file())
        text = entry.read_text(encoding="utf-8")
        self.assertIn("run_session", text)

    def test_hud_runs_on_background_thread(self) -> None:
        mod = LOOPER_SESSION.read_text(encoding="utf-8")
        self.assertIn("start_hud_thread", mod)
        self.assertIn("threading.Thread", mod)
        self.assertIn("daemon=False", mod)
        self.assertIn("os._exit(1)", mod)
        self.assertIn("hud_writer.close()", mod)

    def test_run_session_bench_only_skips_hud_thread(self) -> None:
        sooper = REPO / "scripts" / "sooperlooper"
        body = f"""
import sys
sys.path.insert(0, "{sooper}")
sys.path.insert(0, "{REPO}")
import looper_session as ls
ls.start_hud_thread = lambda _s: (_ for _ in ()).throw(AssertionError("hud started"))
class _FakeSession:
    def start(self):
        return self
ls.SlOscSession = _FakeSession
class _FakeBench:
    @staticmethod
    def run_bench(argv=None, osc_session=None):
        return 0
ls._load_bench_module = lambda: _FakeBench
print("bench-only-ok")
raise SystemExit(ls.run_session(["--bench-only"]))
"""
        result = subprocess.run(
            [sys.executable, "-c", body],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bench-only-ok", result.stdout)




    def test_single_osc_session_module_present(self) -> None:
        path = REPO / "scripts" / "sooperlooper" / "sl_osc_session.py"
        self.assertTrue(path.is_file())
        body = path.read_text(encoding="utf-8")
        self.assertIn("class SlOscSession", body)
        self.assertIn("Refusing to run blind", body)
        self.assertNotIn("MPE_SL_HUD_LISTEN_PORT", body)

    def test_looper_session_wires_shared_session(self) -> None:
        mod = LOOPER_SESSION.read_text(encoding="utf-8")
        self.assertIn("SlOscSession", mod)
        self.assertIn("osc_session=session", mod)

    def test_hud_only_registers_loop_subscriptions(self) -> None:
        mod = LOOPER_SESSION.read_text(encoding="utf-8")
        hud_only = mod.split("if args.hud_only:", 1)[1].split("hud_thread = None", 1)[0]
        self.assertIn("register_hud_loops()", hud_only)
        self.assertIn("register_auto_updates()", hud_only)

if __name__ == "__main__":
    unittest.main()


class LatencyTapTests(unittest.TestCase):
    """Criterion 42 — the instrument must see the sends a pad actually makes."""

    @staticmethod
    def _tap_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "latency_tap_for_test", REPO / "scripts" / "sooperlooper" / "latency_tap.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_tap_pairs_a_pending_pad_with_the_next_hit(self) -> None:
        import time as _time

        mod = self._tap_module()

        class _Inner:
            def __init__(self) -> None:
                self.sent: list[str] = []

            def send_message(self, path: str, args) -> None:
                self.sent.append(path)

        inner = _Inner()
        pending: list[float] = [_time.monotonic()]
        out: list[float] = []
        client = mod.LatencyTapClient(inner, pending, out)
        client.send_message("/sl/0/hit", ["trigger"])
        self.assertEqual(len(out), 1)
        self.assertGreaterEqual(out[0], 0.0)
        self.assertEqual(inner.sent, ["/sl/0/hit"])
        client.send_message("/sl/0/set", ["x"])
        self.assertEqual(len(out), 1, "non-/hit sends must not consume a sample")

    def test_footswitches_are_handed_the_tapped_client(self) -> None:
        """The bug this exists for: hooking _send measured nothing.

        build_footswitches(osc=...) hands the raw client to every footswitch, which
        sends /hit through it directly. A hook in the bench's _send helper never sees
        a pad. Measured on the appliance 2026-08-19: 267 presses, zero samples.
        """
        text = (REPO / "scripts" / "sooperlooper-apc-bench.py").read_text(encoding="utf-8")
        tap = text.index("LatencyTapClient(osc,")
        build = text.index("by_note, footswitches = build_footswitches(")
        self.assertLess(tap, build, "the client must be wrapped before footswitches bind it")
        send_body = text.split("def _send(path: str, a: list) -> None:", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn(
            "midi_osc_pending", send_body, "pairing belongs on the client, not in _send"
        )
