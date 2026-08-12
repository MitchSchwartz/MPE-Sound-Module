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
    ENGINE_STATE_FILE,
    JACKD_SETTLE_SEC,
    MAX_SUPERVISOR_RESTARTS,
    engine_hud_label,
    engine_hud_should_show,
    looper_guard_blocked,
    looper_guard_exit_code,
    read_engine_state,
    reconcile_cooldown_decide,
    resolve_audio_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIO_ENGINE_SH = REPO_ROOT / "scripts" / "lib" / "audio-engine.sh"
ENGINE_GUARD_SH = REPO_ROOT / "scripts" / "lib" / "engine-guard.sh"
SURGE_SERVICE = REPO_ROOT / "config" / "surge-xt-cli.service"
WATCHDOG_SERVICE = REPO_ROOT / "config" / "surge-watchdog.service"


def _bash_env(run_dir: str | None = None, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["MPE_MODULE_REPO"] = str(REPO_ROOT)
    if run_dir is not None:
        env["MPE_RUN_DIR"] = run_dir
        env["MPE_ENGINE_RECONCILE_STATE"] = f"{run_dir}/engine-reconcile.state"
        env["MPE_ENGINE_STATE_FILE"] = f"{run_dir}/engine.state"
        env["MPE_SURGE_STATE_FILE"] = f"{run_dir}/surge.state"
        env["MPE_JACK_STATE_FILE"] = f"{run_dir}/jack.state"
    env.update(extra)
    return env


def _run_bash_script(body: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "test.sh"
        script.write_text("#!/bin/bash\nset -euo pipefail\n" + body, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return subprocess.run(
            [str(script)],
            capture_output=True,
            text=True,
            env=env or _bash_env(),
            check=False,
        )


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


class RuntimeDirectoryPreserveTests(unittest.TestCase):
    """B2 — cooldown state must survive sibling unit restarts."""

    def test_surge_unit_preserves_runtime_dir(self) -> None:
        text = SURGE_SERVICE.read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectory=mpe", text)
        self.assertIn("RuntimeDirectoryPreserve=yes", text)

    def test_watchdog_unit_has_runtime_dir(self) -> None:
        text = WATCHDOG_SERVICE.read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectory=mpe", text)
        self.assertIn("RuntimeDirectoryPreserve=yes", text)


class StateFileLifecycleTests(unittest.TestCase):
    def test_reconcile_counter_survives_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_engine_reconcile_record_restart
mpe_engine_reconcile_record_restart
printf '%s' "$(mpe_engine_reconcile_count)"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "2")

    def test_atomic_engine_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_engine_state_write jack alsa degraded no-server guarded
grep -q '^state=degraded$' "$(mpe_engine_state_file)"
grep -q '^looper=guarded$' "$(mpe_engine_state_file)"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)


class JackLspProbeTests(unittest.TestCase):
    """M4 — both probes treat missing jack_lsp as not-ready."""

    def test_server_ready_fails_without_jack_lsp_even_if_jackd_running(self) -> None:
        body = f"""
pgrep() {{ [ "$2" = jackd ] && return 0; return 1; }}
export -f pgrep
source {AUDIO_ENGINE_SH}
if mpe_jack_server_ready; then exit 9; fi
echo ok
"""
        env = _bash_env()
        env["PATH"] = "/usr/bin:/bin"
        result = _run_bash_script(body, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_surge_on_graph_fails_without_jack_lsp(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
if mpe_surge_on_jack_graph; then exit 9; fi
echo ok
"""
        env = _bash_env()
        env["PATH"] = "/usr/bin:/bin"
        result = _run_bash_script(body, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


class WatchdogReconcileArmTests(unittest.TestCase):
    """B3 — reconcile arms: ok, degraded ALSA no-op, promote, engine=alsa ignored."""

    def test_alsa_engine_ignores_jackd(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
export MPE_AUDIO_ENGINE=alsa
if mpe_engine_is_jack; then exit 9; fi
echo ignored
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.stdout.strip(), "ignored")

    def test_server_down_surge_on_alsa_publishes_degraded_no_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            Path(tmp, "surge.state").write_text("active=alsa\n", encoding="utf-8")
            body = f"""
pgrep() {{ return 1; }}
export -f pgrep
source {AUDIO_ENGINE_SH}
if [ "$(mpe_surge_active_engine)" != alsa ]; then exit 9; fi
mpe_engine_state_write jack alsa degraded no-server "$(mpe_looper_state_label)"
state="$(mpe_engine_state_get state)"
active="$(mpe_engine_state_get active)"
printf '%s:%s' "$state" "$active"
"""
            env["PATH"] = "/usr/bin:/bin"
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "degraded:alsa")

    def test_surge_on_jack_graph_means_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_engine_state_write jack jack ok "" off
printf '%s:%s' "$(mpe_engine_state_get state)" "$(mpe_engine_state_get active)"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.stdout.strip(), "ok:jack")


class EngineGuardShellTests(unittest.TestCase):
    """Guard exit-code split — engine-guard.sh + audio_engine.py parity."""

    def test_guard_refuses_interactively_under_jack(self) -> None:
        body = f"""
source {ENGINE_GUARD_SH}
export MPE_AUDIO_ENGINE=jack
export MPE_LOOPER_ENABLED=1
if mpe_guard_looper_engine test; then exit 9; fi
echo refused
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOOPER-GUARDED", result.stderr)
        self.assertEqual(result.stdout.strip(), "refused")

    def test_guard_allows_alsa(self) -> None:
        body = f"""
source {ENGINE_GUARD_SH}
export MPE_AUDIO_ENGINE=alsa
export MPE_LOOPER_ENABLED=1
mpe_guard_looper_engine test
echo allowed
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "allowed")

    def test_python_guard_matches_shell_policy(self) -> None:
        blocked = looper_guard_blocked(engine="jack", looper_enabled="1")
        self.assertTrue(blocked)
        self.assertEqual(looper_guard_exit_code(looper_service=True), 0)
        self.assertEqual(looper_guard_exit_code(looper_service=False), 1)


class EngineHudReaderTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.state"
            self.assertEqual(read_engine_state(missing), {})
            self.assertFalse(engine_hud_should_show({}))

    def test_partial_file_tolerated(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".state") as fh:
            fh.write("engine=jack\nstate=deg")
            path = Path(fh.name)
        try:
            state = read_engine_state(path)
            self.assertEqual(state["engine"], "jack")
            self.assertTrue(engine_hud_should_show(state))
        finally:
            path.unlink(missing_ok=True)

    def test_looper_guarded_label(self) -> None:
        state = {"engine": "jack", "active": "jack", "state": "ok", "looper": "guarded"}
        self.assertTrue(engine_hud_should_show(state))
        label = engine_hud_label(state)
        self.assertIn("JACK", label)
        self.assertIn("L⛔", label)

    def test_degraded_shows_in_label(self) -> None:
        state = {"active": "alsa", "state": "degraded"}
        self.assertEqual(engine_hud_label(state), "ALSA·deg")

    def test_default_engine_state_path(self) -> None:
        self.assertEqual(ENGINE_STATE_FILE, Path("/run/mpe/engine.state"))


class RunDirFallbackTests(unittest.TestCase):
    def test_unwritable_run_dir_logs_warning(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
export MPE_RUN_DIR=/root/noaccess-mpe-test
dir="$(mpe_run_dir)"
case "$dir" in */mpe) echo fallback ;; *) exit 9 ;; esac
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "fallback")
        self.assertIn("WARNING", result.stderr)


if __name__ == "__main__":
    unittest.main()
