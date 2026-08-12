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
SURGE_WATCHDOG_SH = REPO_ROOT / "scripts" / "surge-watchdog.sh"
SURGE_SERVICE = REPO_ROOT / "config" / "surge-xt-cli.service"
WATCHDOG_SERVICE = REPO_ROOT / "config" / "surge-watchdog.service"
JACKD_SERVICE = REPO_ROOT / "config" / "mpe-jackd.service"


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

    def test_jackd_unit_preserves_runtime_dir(self) -> None:
        text = JACKD_SERVICE.read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectory=mpe", text)
        self.assertIn("RuntimeDirectoryPreserve=yes", text)


class JackdStartLimitTests(unittest.TestCase):
    """DAC replug recovery (9258b68/5717d85) + skip jackd when Surge on ALSA (finding 11)."""

    def test_jackd_unit_disables_start_rate_limit(self) -> None:
        text = JACKD_SERVICE.read_text(encoding="utf-8")
        self.assertIn("Restart=always", text)
        self.assertIn("StartLimitIntervalSec=0", text)

    def test_jackd_unit_disables_alsa_audio_reservation(self) -> None:
        text = JACKD_SERVICE.read_text(encoding="utf-8")
        self.assertIn("JACK_NO_AUDIO_RESERVATION=1", text)

    def test_jackd_engine_condition_is_executable_in_git(self) -> None:
        import subprocess

        result = subprocess.run(
            ["git", "ls-files", "-s", "scripts/jackd-engine-condition.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        mode = result.stdout.split()[0] if result.stdout.strip() else ""
        self.assertEqual(mode, "100755", msg="ExecCondition must be executable in git")

    def test_graph_restart_skips_jackd_when_surge_on_alsa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            Path(tmp, "surge.state").write_text("active=alsa\n", encoding="utf-8")
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_systemctl() {{ printf '%s\\n' "$*" >> "{tmp}/systemctl.log"; return 0; }}
export -f mpe_systemctl
if mpe_restart_audio_graph; then echo skipped; else exit 9; fi
if [ -f "{tmp}/systemctl.log" ]; then cat "{tmp}/systemctl.log"; fi
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skipped", result.stdout)
            self.assertNotIn("mpe-jackd", result.stdout)

    def test_skip_is_distinguishable_from_restart(self) -> None:
        """restart-audio-graph.sh logged "restarted" on the skip path, which is
        the one path an operator reads while debugging a degraded appliance."""
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            Path(tmp, "surge.state").write_text("active=alsa\n", encoding="utf-8")
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_systemctl() {{ return 0; }}
export -f mpe_systemctl
mpe_restart_audio_graph
printf 'action=%s\\n' "$MPE_AUDIO_GRAPH_ACTION"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("action=skipped", result.stdout)

    def test_graph_restart_resets_failed_state_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_systemctl() {{ printf '%s\\n' "$*" >> "{tmp}/systemctl.log"; return 0; }}
export -f mpe_systemctl
mpe_engine_reconcile_record_restart
mpe_restart_audio_graph
printf 'count=%s\\n' "$(mpe_engine_reconcile_count)"
cat "{tmp}/systemctl.log"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.strip().splitlines()
            self.assertEqual(lines[0], "count=0")
            self.assertEqual(lines[1], "reset-failed mpe-jackd.service")
            self.assertEqual(lines[2], "restart --no-block mpe-jackd.service")


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

    def test_empty_active_engine_does_not_kill_caller(self) -> None:
        """A status publisher must never exit the shell that called it.

        `${2:?}` here killed surge-watchdog.sh mid-restart when the state file
        was unreadable, leaving Surge crashed and unsupervised.
        """
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_engine_state_write jack "$(cat /nonexistent 2>/dev/null)" recovering surge-failed off
printf 'SURVIVED'
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SURVIVED", result.stdout)


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


class AlsafallbackJackdStopTests(unittest.TestCase):
    """Finding 7 — stop jackd before ALSA tier selection when jackd holds the device."""

    def test_release_stops_jackd_nonblocking_then_polls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            body = f"""
source {AUDIO_ENGINE_SH}
_calls=""
_jackd_gone=0
pgrep() {{
    if [ "$2" = jackd ]; then
        [ "$_jackd_gone" = 1 ] && return 1
        return 0
    fi
    return 1
}}
export -f pgrep
mpe_systemctl() {{
    _calls="$_calls $*"
    case "$*" in
        *stop* ) _jackd_gone=1 ;;
    esac
    return 0
}}
export -f mpe_systemctl
mpe_release_audio_device_for_alsa || exit 9
printf '%s' "$_calls"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("stop --no-block mpe-jackd.service", result.stdout)

    def test_start_surge_cli_release_before_alsa_select(self) -> None:
        """Finding 7 — production chokepoint must call release before select_alsa_device."""
        text = (REPO_ROOT / "scripts" / "start-surge-cli.sh").read_text(encoding="utf-8")
        release_pos = text.find("mpe_release_audio_device_for_alsa")
        select_pos = text.find("if select_alsa_device; then")
        self.assertGreater(release_pos, 0, "missing mpe_release_audio_device_for_alsa")
        self.assertGreater(select_pos, release_pos, "release must precede select_alsa_device")

    def test_fallback_junction_stops_jackd_before_device_select(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            body = f"""
source {AUDIO_ENGINE_SH}
_select_called=0
pgrep() {{ [ "$2" = jackd ] && return 0; return 1; }}
export -f pgrep
mpe_systemctl() {{ return 0; }}
export -f mpe_systemctl
mpe_jack_server_running() {{ pgrep -x jackd >/dev/null 2>&1; }}
mpe_release_audio_device_for_alsa() {{
    mpe_systemctl stop --no-block mpe-jackd.service
    pgrep() {{ return 1; }}
    export -f pgrep
    return 0
}}
select_alsa_device() {{ _select_called=1; return 0; }}
engine_log() {{ :; }}
AUDIO_ENGINE=jack
ACTIVE_ENGINE=""
FALLBACK_ACTION=""
if [ -z "$ACTIVE_ENGINE" ]; then
    if [ "$AUDIO_ENGINE" = jack ] && mpe_jack_server_running; then
        if mpe_release_audio_device_for_alsa; then
            FALLBACK_ACTION="stopped-jackd"
        else
            FALLBACK_ACTION="jackd-still-running"
        fi
    fi
    select_alsa_device
fi
printf 'select=%s action=%s' "$_select_called" "$FALLBACK_ACTION"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "select=1 action=stopped-jackd")


class WatchdogReconcileArmTests(unittest.TestCase):
    """B3 — reconcile arms via surge-watchdog.sh _reconcile_engine (finding 8)."""

    def _run_reconcile(self, *, env: dict[str, str], stubs: str) -> subprocess.CompletedProcess[str]:
        body = f"""
source {SURGE_WATCHDOG_SH}
{stubs}
_reconcile_engine
printf '%s:%s' "$(mpe_engine_state_get state)" "$(mpe_engine_state_get active)"
"""
        return _run_bash_script(body, env=env)

    def test_alsa_engine_ignores_jackd(self) -> None:
        body = f"""
source {SURGE_WATCHDOG_SH}
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
            stubs = """
pgrep() { return 1; }
export -f pgrep
mpe_jack_server_ready() { return 1; }
mpe_surge_on_jack_graph() { return 1; }
_supervisor_restart_surge() { echo RESTART >&2; return 0; }
export -f _supervisor_restart_surge
"""
            env["PATH"] = "/usr/bin:/bin"
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "degraded:alsa")
            self.assertNotIn("RESTART", result.stderr)

    def test_surge_on_jack_graph_means_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp, MPE_AUDIO_ENGINE="jack")
            stubs = """
mpe_surge_on_jack_graph() { return 0; }
"""
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
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
        if os.geteuid() == 0:
            self.skipTest("root can write /root/noaccess-mpe-test — test needs non-root")
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
