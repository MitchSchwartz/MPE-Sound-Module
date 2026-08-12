"""Unit tests for patch_browser/audio_engine.py — engine default, looper guard, cooldown."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from patch_browser.audio_engine import (
    COOLDOWN_SEC,
    DEFAULT_ENGINE,
    JACKD_SETTLE_SEC,
    MAX_SUPERVISOR_RESTARTS,
    looper_guard_blocked,
    looper_guard_exit_code,
    reconcile_cooldown_decide,
    resolve_audio_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIO_ENGINE_SH = REPO_ROOT / "scripts" / "lib" / "audio-engine.sh"


class ResolveAudioEngineTests(unittest.TestCase):
    def test_defaults_to_jack_when_unset(self) -> None:
        self.assertEqual(resolve_audio_engine(None), "jack")
        self.assertEqual(resolve_audio_engine(""), "jack")

    def test_honours_explicit_alsa(self) -> None:
        self.assertEqual(resolve_audio_engine("alsa"), "alsa")

    def test_honours_explicit_jack(self) -> None:
        self.assertEqual(resolve_audio_engine("jack"), "jack")


class LooperGuardTests(unittest.TestCase):
    def test_blocked_when_jack_and_looper_enabled(self) -> None:
        self.assertTrue(looper_guard_blocked(engine="jack", looper_enabled="1"))

    def test_not_blocked_when_alsa(self) -> None:
        self.assertFalse(looper_guard_blocked(engine="alsa", looper_enabled="1"))

    def test_not_blocked_when_looper_disabled(self) -> None:
        self.assertFalse(looper_guard_blocked(engine="jack", looper_enabled="0"))

    def test_service_exit_zero(self) -> None:
        self.assertEqual(looper_guard_exit_code(looper_service=True), 0)

    def test_interactive_exit_nonzero(self) -> None:
        self.assertEqual(looper_guard_exit_code(looper_service=False, invocation_id=None), 1)

    def test_invocation_id_backstop_exits_zero(self) -> None:
        self.assertEqual(looper_guard_exit_code(looper_service=False, invocation_id="abc"), 0)


class ReconcileCooldownTests(unittest.TestCase):
    def test_first_restart_is_immediate(self) -> None:
        action, _ = reconcile_cooldown_decide(
            1000,
            last_supervisor_restart=None,
            supervisor_restarts_without_ok=0,
            jackd_last_start=900,
        )
        self.assertEqual(action, "proceed")

    def test_respects_90s_cooldown(self) -> None:
        action, _ = reconcile_cooldown_decide(
            1000,
            last_supervisor_restart=950,
            supervisor_restarts_without_ok=1,
            jackd_last_start=800,
        )
        self.assertEqual(action, "skip_cooldown")

    def test_skips_while_jackd_settling(self) -> None:
        action, _ = reconcile_cooldown_decide(
            1000,
            last_supervisor_restart=800,
            supervisor_restarts_without_ok=0,
            jackd_last_start=990,
        )
        self.assertEqual(action, "skip_jackd_settling")

    def test_escalates_after_three_restarts(self) -> None:
        action, _ = reconcile_cooldown_decide(
            5000,
            last_supervisor_restart=4000,
            supervisor_restarts_without_ok=MAX_SUPERVISOR_RESTARTS,
            jackd_last_start=1000,
        )
        self.assertEqual(action, "escalate_failed")

    def test_constants_match_spec(self) -> None:
        self.assertEqual(COOLDOWN_SEC, 90)
        self.assertEqual(JACKD_SETTLE_SEC, 15)
        self.assertEqual(MAX_SUPERVISOR_RESTARTS, 3)
        self.assertEqual(DEFAULT_ENGINE, "jack")


def _run_bash_reconcile(now: int, last: int, count: int, jackd: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "decide.sh"
        script.write_text(
            "#!/bin/bash\n"
            f"source {AUDIO_ENGINE_SH}\n"
            f"mpe_engine_reconcile_decision {now} {last} {count} {jackd}\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        env = os.environ.copy()
        env["MPE_MODULE_REPO"] = str(REPO_ROOT)
        result = subprocess.run(
            [str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return result.stdout.strip()


class BashReconcileParityTests(unittest.TestCase):
    def test_bash_first_restart(self) -> None:
        self.assertEqual(_run_bash_reconcile(1000, 0, 0, 900), "restart")

    def test_bash_cooldown(self) -> None:
        self.assertEqual(_run_bash_reconcile(1000, 950, 1, 800), "cooldown")

    def test_bash_jackd_settling(self) -> None:
        self.assertEqual(_run_bash_reconcile(1000, 800, 0, 990), "jackd-settling")

    def test_bash_failed(self) -> None:
        self.assertEqual(_run_bash_reconcile(5000, 4000, 3, 1000), "failed")


if __name__ == "__main__":
    unittest.main()
