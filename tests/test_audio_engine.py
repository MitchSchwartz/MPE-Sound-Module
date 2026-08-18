"""Unit tests for patch_browser/audio_engine.py — looper guard, cooldown, HUD.

JACK is the only audio engine (spec D3, amended 2026-08-13 — ALSA removed
entirely, not just its automatic fallback). Tests that only existed to cover
the ALSA path or dual-engine reconcile arms are gone; engine-selection-failure
handling (jackd never comes up → hard failure) is covered and adapted here.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.hermetic_env import isolated_path_only

from patch_browser.audio_engine import (
    COOLDOWN_SEC,
    ENGINE_STATE_FILE,
    JACKD_SETTLE_SEC,
    LOOPER_GUARD_MESSAGE,
    MAX_SUPERVISOR_RESTARTS,
    engine_hud_label,
    engine_hud_should_show,
    looper_guard_blocked,
    looper_guard_exit_code,
    read_engine_state,
    reconcile_cooldown_decide,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIO_ENGINE_SH = REPO_ROOT / "scripts" / "lib" / "audio-engine.sh"
ENGINE_GUARD_SH = REPO_ROOT / "scripts" / "lib" / "engine-guard.sh"
SURGE_WATCHDOG_SH = REPO_ROOT / "scripts" / "surge-watchdog.sh"
START_SURGE_CLI_SH = REPO_ROOT / "scripts" / "start-surge-cli.sh"
RESTART_AUDIO_GRAPH_SH = REPO_ROOT / "scripts" / "restart-audio-graph.sh"
START_JACKD_SH = REPO_ROOT / "scripts" / "start-jackd.sh"
SET_SURGE_AUDIO_SH = REPO_ROOT / "scripts" / "set-surge-audio.sh"
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


class LooperGuardTests(unittest.TestCase):
    """JACK is the only engine, so the guard no longer takes an engine param."""

    def test_blocked_when_looper_enabled(self) -> None:
        self.assertTrue(looper_guard_blocked(looper_enabled="1"))

    def test_not_blocked_when_looper_disabled(self) -> None:
        self.assertFalse(looper_guard_blocked(looper_enabled="0"))

    def test_not_blocked_when_looper_unset(self) -> None:
        self.assertFalse(looper_guard_blocked(looper_enabled=None))

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

    def test_respects_cooldown_window(self) -> None:
        action, _ = reconcile_cooldown_decide(
            1000,
            last_supervisor_restart=980,
            supervisor_restarts_without_ok=1,
            jackd_last_start=800,
        )
        self.assertEqual(action, "skip_cooldown")

    def test_skips_while_jackd_settling(self) -> None:
        # Settle window is 5s post-amendment (was 15s under the ALSA-contention
        # hazard, which no longer exists). jackd started 3s ago — still settling.
        action, _ = reconcile_cooldown_decide(
            1000,
            last_supervisor_restart=800,
            supervisor_restarts_without_ok=0,
            jackd_last_start=997,
        )
        self.assertEqual(action, "skip_jackd_settling")

    def test_proceeds_once_settle_window_elapses(self) -> None:
        # jackd started 10s ago — past the 5s settle window and past cooldown
        # (last restart 200s ago), so the supervisor may act.
        action, _ = reconcile_cooldown_decide(
            1000,
            last_supervisor_restart=800,
            supervisor_restarts_without_ok=0,
            jackd_last_start=990,
        )
        self.assertEqual(action, "proceed")

    def test_escalates_after_three_restarts(self) -> None:
        action, _ = reconcile_cooldown_decide(
            5000,
            last_supervisor_restart=4000,
            supervisor_restarts_without_ok=MAX_SUPERVISOR_RESTARTS,
            jackd_last_start=1000,
        )
        self.assertEqual(action, "escalate_failed")

    def test_constants_match_spec(self) -> None:
        self.assertEqual(COOLDOWN_SEC, 30)
        self.assertEqual(JACKD_SETTLE_SEC, 5)
        self.assertEqual(MAX_SUPERVISOR_RESTARTS, 3)


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
        self.assertEqual(_run_bash_reconcile(1000, 980, 1, 800), "cooldown")

    def test_bash_jackd_settling(self) -> None:
        self.assertEqual(_run_bash_reconcile(1000, 800, 0, 997), "jackd-settling")

    def test_bash_proceeds_once_settle_window_elapses(self) -> None:
        self.assertEqual(_run_bash_reconcile(1000, 800, 0, 990), "restart")

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

    def test_surge_unit_wants_watchdog(self) -> None:
        sections = _systemd_sections(SURGE_SERVICE.read_text(encoding="utf-8"))
        wants = _systemd_unit_directives(sections, "Wants")
        self.assertIn("surge-watchdog.service", wants)

    def test_watchdog_not_bound_to_surge(self) -> None:
        # Gate C 2*: the watchdog must survive a hard Surge failure to promote
        # once jackd recovers. BindsTo kills the supervisor with the supervised.
        sections = _systemd_sections(WATCHDOG_SERVICE.read_text(encoding="utf-8"))
        self.assertEqual([], _systemd_unit_directives(sections, "BindsTo"))
        after = _systemd_unit_directives(sections, "After")
        self.assertIn("surge-xt-cli.service", after)

    def test_watchdog_restarts_always(self) -> None:
        text = WATCHDOG_SERVICE.read_text(encoding="utf-8")
        self.assertIn("Restart=always", text)


def _systemd_sections(text: str) -> dict[str, list[str]]:
    """Parse a unit file into section -> non-empty, non-comment lines."""
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return sections


def _systemd_unit_directives(
    sections: dict[str, list[str]], key: str, *, section: str = "Unit"
) -> list[str]:
    """Collect values for a unit directive (space-separated lists and repeated lines)."""
    values: list[str] = []
    prefix = f"{key}="
    for line in sections.get(section, []):
        if line.startswith(prefix):
            rest = line[len(prefix) :].strip()
            if rest:
                values.extend(rest.split())
    return values


class SurgeStartLimitUnitTests(unittest.TestCase):
    """StartLimit* must live in [Unit] — systemd ignores them under [Service]."""

    def test_start_limit_keys_in_unit_section(self) -> None:
        sections = _systemd_sections(SURGE_SERVICE.read_text(encoding="utf-8"))
        unit_lines = sections.get("Unit", [])
        service_lines = sections.get("Service", [])
        self.assertIn("StartLimitBurst=5", unit_lines)
        self.assertIn("StartLimitIntervalSec=300", unit_lines)
        self.assertNotIn("StartLimitBurst=5", service_lines)
        self.assertNotIn("StartLimitIntervalSec=300", service_lines)


class JackdStartLimitTests(unittest.TestCase):
    """DAC replug recovery (9258b68/5717d85). ALSA-skip tests (finding 11) and the
    jackd-engine-condition executable-bit test are gone with ALSA removal — jackd
    now starts unconditionally, so there is no ExecCondition script to check."""

    def test_jackd_unit_disables_start_rate_limit(self) -> None:
        text = JACKD_SERVICE.read_text(encoding="utf-8")
        self.assertIn("Restart=always", text)
        self.assertIn("StartLimitIntervalSec=0", text)

    def test_jackd_unit_disables_alsa_audio_reservation(self) -> None:
        text = JACKD_SERVICE.read_text(encoding="utf-8")
        self.assertIn("JACK_NO_AUDIO_RESERVATION=1", text)

    def test_jackd_unit_has_no_engine_condition(self) -> None:
        """ExecCondition=jackd-engine-condition.sh is gone — jackd always starts."""
        text = JACKD_SERVICE.read_text(encoding="utf-8")
        self.assertNotIn("ExecCondition=", text)
        self.assertNotIn("jackd-engine-condition.sh", text)

    def test_jackd_engine_condition_script_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "scripts" / "jackd-engine-condition.sh").exists())

    def test_graph_restart_resets_failed_state_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
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

    def test_audio_graph_unit_is_always_jackd(self) -> None:
        """Single engine: mpe_audio_graph_unit no longer branches on anything."""
        body = f"""
source {AUDIO_ENGINE_SH}
mpe_audio_graph_unit
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "mpe-jackd.service")

    def test_planned_promote_sync_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            on_graph = Path(tmp) / "on-graph"
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_jack_server_ready() {{ [ "$1" = "1" ] && return 0; return 1; }}
mpe_surge_on_jack_graph() {{ [ -f "{on_graph}" ]; }}
mpe_restart_audio_graph() {{ printf 'graph-restart\\n'; return 0; }}
mpe_systemctl() {{
  printf '%s\\n' "$*" >> "{tmp}/systemctl.log"
  case "$*" in
    restart\\ surge-xt-cli.service) touch "{on_graph}" ;;
  esac
  return 0
}}
export -f mpe_jack_server_ready mpe_surge_on_jack_graph mpe_restart_audio_graph mpe_systemctl
mpe_promote_surge_planned settings-change
printf 'state=%s\\n' "$(mpe_engine_state_get state)"
grep -c 'restart surge-xt-cli.service' "{tmp}/systemctl.log" || true
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.strip().splitlines()
            self.assertEqual(lines[0], "graph-restart")
            self.assertEqual(lines[1], "state=ok")
            self.assertEqual(lines[2], "1")


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

    def test_mpe_state_get_last_match_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            state_file = Path(tmp) / "engine.state"
            state_file.write_text("state=recovering\nstate=ok\nactive=jack\n", encoding="utf-8")
            body = f"""
source {AUDIO_ENGINE_SH}
printf 'state=%s active=%s\n' "$(mpe_state_get '{state_file}' state)" "$(mpe_state_get '{state_file}' active)"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "state=ok active=jack")

    def test_atomic_engine_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_engine_state_write jack none recovering no-server guarded
grep -q '^state=recovering$' "$(mpe_engine_state_file)"
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

    def test_atomic_write_warns_on_unwritable_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ro_dir = Path(tmp) / "readonly"
            ro_dir.mkdir()
            ro_dir.chmod(0o555)
            target = ro_dir / "engine.state"
            body = f"""
source {AUDIO_ENGINE_SH}
rc=0
mpe_state_write_atomic "{target}" "engine=jack" "state=ok" || rc=$?
printf 'rc=%s\\n' "$rc"
"""
            try:
                result = _run_bash_script(body, env=_bash_env(tmp))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("rc=1", result.stdout)
                self.assertIn("WARNING:", result.stderr)
                self.assertIn("failed to write", result.stderr)
            finally:
                ro_dir.chmod(0o755)

    def test_engine_state_write_survives_atomic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ro_dir = Path(tmp) / "readonly"
            ro_dir.mkdir()
            ro_dir.chmod(0o555)
            body = f"""
source {AUDIO_ENGINE_SH}
export MPE_ENGINE_STATE_FILE="{ro_dir}/engine.state"
mpe_engine_state_write jack jack ok "" off
printf 'SURVIVED'
"""
            try:
                result = _run_bash_script(body, env=_bash_env(tmp))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SURVIVED", result.stdout)
            finally:
                ro_dir.chmod(0o755)


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
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env()
            env["PATH"] = isolated_path_only(Path(tmp))
            result = _run_bash_script(body, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_surge_on_graph_fails_without_jack_lsp(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
if mpe_surge_on_jack_graph; then exit 9; fi
echo ok
"""
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env()
            env["PATH"] = isolated_path_only(Path(tmp))
            result = _run_bash_script(body, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


    def test_surge_on_jack_graph_calls_jack_lsp_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            count_file = Path(tmp) / "count"
            count_file.write_text("0\n", encoding="utf-8")
            jack_stub = bin_dir / "jack_lsp"
            jack_stub.write_text(
                f"""#!/bin/sh
n=$(cat "{count_file}")
echo $((n + 1)) > "{count_file}"
printf 'surge-xt\n'
""",
                encoding="utf-8",
            )
            jack_stub.chmod(jack_stub.stat().st_mode | stat.S_IXUSR)
            body = f"""
source {AUDIO_ENGINE_SH}
pgrep() {{ [ "$1" = "-x" ] && [ "$2" = "jackd" ] && return 0; return 1; }}
export -f pgrep
timeout() {{ shift; "$@"; }}
export -f timeout
export PATH="{bin_dir}:$PATH"
if mpe_surge_on_jack_graph; then echo yes; else echo no; fi
cat "{count_file}"
"""
            result = _run_bash_script(body)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().splitlines()[0], "yes")
            self.assertEqual(result.stdout.strip().splitlines()[1], "1")

    def test_surge_on_jack_graph_fails_on_empty_port_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            jack_stub = bin_dir / "jack_lsp"
            jack_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            jack_stub.chmod(jack_stub.stat().st_mode | stat.S_IXUSR)
            body = f"""
source {AUDIO_ENGINE_SH}
pgrep() {{ [ "$1" = "-x" ] && [ "$2" = "jackd" ] && return 0; return 1; }}
export -f pgrep
timeout() {{ shift; "$@"; }}
export -f timeout
export PATH="{bin_dir}:$PATH"
if mpe_surge_on_jack_graph; then exit 9; fi
echo ok
"""
            result = _run_bash_script(body)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "ok")

    def test_jack_lsp_runs_as_graph_owner_when_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "sudo.log"
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            jack_stub = bin_dir / "jack_lsp"
            jack_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            jack_stub.chmod(jack_stub.stat().st_mode | stat.S_IXUSR)
            env = _bash_env()
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
            body = f"""
source {AUDIO_ENGINE_SH}
id() {{ case "$1" in -u) echo 0 ;; -un) echo root ;; *) command id "$@" ;; esac; }}
export -f id
timeout() {{ shift; "$@"; }}
export -f timeout
sudo() {{ printf '%s\\n' "$*" >> "{log}"; :; }}
export -f sudo
export SUDO_USER=mitch
export MPE_PI_USER=mitch
mpe_jack_lsp >/dev/null 2>&1 || true
cat "{log}"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("-u mitch", result.stdout)


class NoAlsaPathTests(unittest.TestCase):
    """ALSA is not a reachable audio engine anywhere in the codebase (2026-08-12)."""

    def test_start_surge_cli_has_no_alsa_branch(self) -> None:
        text = START_SURGE_CLI_SH.read_text(encoding="utf-8")
        for token in (
            "select_alsa_device",
            "ALSA_FAIL_REASON",
            "ENGINE-FALLBACK",
            "MPE_AUDIO_ENGINE",
            "mpe_release_audio_device_for_alsa",
            "--buffer-size=",
        ):
            self.assertNotIn(token, text, f"start-surge-cli.sh still references {token!r}")

    def test_audio_engine_lib_has_no_engine_selection(self) -> None:
        text = AUDIO_ENGINE_SH.read_text(encoding="utf-8")
        # ${MPE_AUDIO_ENGINE (a read) is banned; the bare name may still appear
        # in comments documenting that it was retired — that is not a reader.
        self.assertNotIn("${MPE_AUDIO_ENGINE", text, "audio-engine.sh still reads $MPE_AUDIO_ENGINE")
        for token in (
            "mpe_audio_engine()",
            "mpe_engine_is_jack()",
            "mpe_release_audio_device_for_alsa",
            "mpe_surge_active_engine",
            "mpe_jackd_unit_masked",
            "mpe_jackd_unit_seeking_start",
            "MPE_AUDIO_GRAPH_ACTION",
        ):
            self.assertNotIn(token, text, f"audio-engine.sh still references {token!r}")

    def test_surge_watchdog_has_no_alsa_reconcile_arm(self) -> None:
        text = SURGE_WATCHDOG_SH.read_text(encoding="utf-8")
        for token in ("active=alsa", "mpe_surge_active_engine", "release-alsa-for-jackd", "degraded"):
            self.assertNotIn(token, text, f"surge-watchdog.sh still references {token!r}")

    def test_surge_watchdog_uses_epochseconds(self) -> None:
        text = SURGE_WATCHDOG_SH.read_text(encoding="utf-8")
        self.assertIn("now=$EPOCHSECONDS", text)
        self.assertNotIn("now=$(date +%s)", text)

    def test_surge_watchdog_reconcile_short_circuits_on_ok_surge(self) -> None:
        text = SURGE_WATCHDOG_SH.read_text(encoding="utf-8")
        self.assertIn('systemctl is-active --quiet "$SURGE_SERVICE"', text)
        self.assertIn('[ "$state" = ok ] && [ "$active" = jack ]', text)
        self.assertIn("JACK_PROBE_INTERVAL_S", text)
        self.assertIn("_last_jack_probe=$EPOCHSECONDS", text)
        self.assertIn('JACK_PROBE_INTERVAL_S="${MPE_JACK_PROBE_INTERVAL_S:-10}"', text)

    def test_surge_watchdog_looper_reconcile_batched_and_throttled(self) -> None:
        text = SURGE_WATCHDOG_SH.read_text(encoding="utf-8")
        self.assertIn("_reconcile_looper_units_if_needed", text)
        self.assertIn("LOOPER_RECONCILE_INTERVAL_S", text)
        self.assertIn('systemctl is-active "${units[@]}"', text)
        self.assertNotIn(
            "python3 \"$MPE_MODULE_REPO/scripts/ensure-looper-units-running.py\" >/dev/null 2>&1 || true\n    fi\n\n    sleep 5",
            text,
        )

    def test_engine_guard_offers_no_engine_switch(self) -> None:
        text = ENGINE_GUARD_SH.read_text(encoding="utf-8")
        self.assertNotIn("MPE_AUDIO_ENGINE", text)
        self.assertNotIn("engine set alsa", text)

    def test_set_surge_audio_has_no_engine_branch(self) -> None:
        """Regression: this caller still invoked the deleted mpe_audio_engine()
        after the library removed it — a real bug caught by this sweep, not a
        hypothetical. It must unconditionally take the graph-restart path."""
        text = SET_SURGE_AUDIO_SH.read_text(encoding="utf-8")
        self.assertNotIn("mpe_audio_engine", text)
        self.assertIn("mpe_promote_surge_planned", text)
        self.assertNotIn("systemctl restart surge-xt-cli.service", text)

    def test_set_surge_audio_is_valid_bash(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(SET_SURGE_AUDIO_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class StartSurgeCliFailureTests(unittest.TestCase):
    """Jack-mode failure exits non-zero and touches no ALSA path (spec D3 amended)."""

    def _run_start_surge_cli(self, *, env: dict[str, str], stubs: str) -> subprocess.CompletedProcess[str]:
        # start-surge-cli.sh is a script, not a sourceable library, and needs a
        # real SURGE_CLI binary + USER_DEFAULTS setup to run end-to-end. This
        # exercises the exact state-publishing sequence its failure branch
        # runs (spec D3 hard failure), matching the real script line-for-line;
        # NoAlsaPathTests statically confirms the real script has no other branch.
        body = f"""
LOG_FILE="{env.get('MPE_RUN_DIR', '/tmp')}/surge-cli.log"
{stubs}
source {AUDIO_ENGINE_SH}
mpe_wait_for_jack_server() {{ return 1; }}
ENGINE_REASON="no-server"
mpe_publish_jack_engine_failure "$ENGINE_REASON"
exit 1
"""
        return _run_bash_script(body, env=env)

    def test_jack_failure_exits_nonzero_and_publishes_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            result = self._run_start_surge_cli(env=env, stubs="")
            self.assertEqual(result.returncode, 1)
            state = Path(tmp, "engine.state").read_text(encoding="utf-8")
            self.assertIn("engine=jack", state)
            self.assertIn("active=none", state)
            self.assertIn("state=failed", state)

    def test_jack_failure_never_calls_alsa_detection(self) -> None:
        """Static guarantee: no code path in the script can reach ALSA device
        selection, because select_alsa_device (and the branch that called it)
        do not exist post-amendment — see NoAlsaPathTests. detect-audio-device.sh
        is a separate, still-legitimate tiering script used by jackd's own
        device pick (detect-jack-device.sh) and calibration; start-surge-cli.sh
        may mention it in a comment but must never invoke it directly."""
        text = START_SURGE_CLI_SH.read_text(encoding="utf-8")
        self.assertNotIn("detect-audio-device.sh\"", text)


class JackLspPathTests(unittest.TestCase):
    def test_jack_lsp_uses_resolved_absolute_path(self) -> None:
        text = AUDIO_ENGINE_SH.read_text(encoding="utf-8")
        self.assertIn("_mpe_jack_lsp_bin", text)
        self.assertIn('"$_MPE_JACK_LSP_BIN"', text)
        self.assertNotIn('timeout "$timeout_s" jack_lsp "$@"', text)


class WatchdogReconcileArmTests(unittest.TestCase):
    """B3 — reconcile arms via surge-watchdog.sh _reconcile_engine (finding 8).
    Single-engine: on graph -> ok; ready but off graph -> promote; not ready ->
    wait out the budget then treat jackd as down and restart Surge."""

    def _run_reconcile(self, *, env: dict[str, str], stubs: str) -> subprocess.CompletedProcess[str]:
        body = f"""
source {SURGE_WATCHDOG_SH}
{stubs}
_reconcile_engine
printf '%s:%s' "$(mpe_engine_state_get state)" "$(mpe_engine_state_get active)"
"""
        return _run_bash_script(body, env=env)

    def test_surge_on_jack_graph_means_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            stubs = """
mpe_surge_on_jack_graph() { return 0; }
"""
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "ok:jack")

    def test_reconcile_skips_jack_probe_when_surge_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            engine = Path(tmp) / "engine.state"
            engine.write_text(
                "engine=jack\nactive=jack\nstate=ok\nupdated=1\nlooper=off\n",
                encoding="utf-8",
            )
            stubs = """
_last_jack_probe=$EPOCHSECONDS
JACK_PROBE_INTERVAL_S=10
mpe_jack_server_ready() { echo JACK_LSP_PROBE >&2; return 1; }
mpe_surge_on_jack_graph() { echo GRAPH_PROBE >&2; return 1; }
export -f mpe_jack_server_ready mpe_surge_on_jack_graph
systemctl() {
  case "$*" in
    is-active*surge-xt-cli*) return 0 ;;
  esac
  return 1
}
export -f systemctl
"""
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "ok:jack")
            self.assertNotIn("JACK_LSP_PROBE", result.stderr)
            self.assertNotIn("GRAPH_PROBE", result.stderr)

    def test_reconcile_probes_graph_again_after_the_interval(self) -> None:
        """A short-circuit that never re-probes cannot see an orphaned JACK client."""
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            engine = Path(tmp) / "engine.state"
            engine.write_text(
                "engine=jack\nactive=jack\nstate=ok\nupdated=1\nlooper=off\n",
                encoding="utf-8",
            )
            stubs = """
_last_jack_probe=0
JACK_PROBE_INTERVAL_S=10
mpe_surge_on_jack_graph() { echo GRAPH_PROBE >&2; return 0; }
export -f mpe_surge_on_jack_graph
systemctl() { case "$*" in is-active*surge-xt-cli*) return 0 ;; esac; return 1; }
export -f systemctl
"""
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("GRAPH_PROBE", result.stderr, "stale short-circuit never re-probes")

    def test_jackd_never_ready_restarts_surge_as_jackd_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            env["MPE_RECONCILE_BUDGET_SEC"] = "0"
            stubs = """
mpe_surge_on_jack_graph() { return 1; }
mpe_jack_server_ready() { return 1; }
_supervisor_restart_surge() { echo "RESTART:$1" >&2; return 0; }
export -f _supervisor_restart_surge
"""
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RESTART:jackd-down", result.stderr)

    def test_jack_ready_but_surge_off_graph_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            stubs = """
mpe_surge_on_jack_graph() { return 1; }
mpe_jack_server_ready() { return 0; }
_supervisor_restart_surge() { echo "RESTART:$1" >&2; return 0; }
export -f _supervisor_restart_surge
"""
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RESTART:promote-to-jack", result.stderr)

    def test_reconcile_defers_supervisor_restart_during_planned_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            Path(tmp, "planned-promote").write_text("1\n", encoding="utf-8")
            stubs = """
mpe_surge_on_jack_graph() { return 1; }
mpe_jack_server_ready() { return 0; }
_supervisor_restart_surge() { echo "RESTART:$1" >&2; return 0; }
export -f _supervisor_restart_surge
"""
            result = self._run_reconcile(env=env, stubs=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("RESTART:", result.stderr)

    def test_supervisor_restart_defers_during_planned_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            Path(tmp, "planned-promote").write_text("1\n", encoding="utf-8")
            body = f"""
source {SURGE_WATCHDOG_SH}
mpe_systemctl() {{ printf '%s\\n' "$*" >> "{tmp}/systemctl.log"; return 0; }}
export -f mpe_systemctl
if _supervisor_restart_surge promote-to-jack; then exit 9; fi
test ! -s "{tmp}/systemctl.log"
echo deferred
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "deferred")

    def test_reconcile_survives_unwritable_state_publish(self) -> None:
        """State publish failure must not abort the supervisor reconcile path."""
        with tempfile.TemporaryDirectory() as tmp:
            ro_dir = Path(tmp) / "readonly"
            ro_dir.mkdir()
            ro_dir.chmod(0o555)
            env = _bash_env(tmp)
            env["MPE_ENGINE_STATE_FILE"] = str(ro_dir / "engine.state")
            body = f"""
source {SURGE_WATCHDOG_SH}
mpe_surge_on_jack_graph() {{ return 0; }}
_reconcile_engine
printf 'SURVIVED'
"""
            try:
                result = _run_bash_script(body, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SURVIVED", result.stdout)
                self.assertIn("WARNING:", result.stderr)
                self.assertIn("failed to write", result.stderr)
            finally:
                ro_dir.chmod(0o755)


class EngineGuardShellTests(unittest.TestCase):
    """Guard exit-code split — engine-guard.sh + audio_engine.py parity."""

    def test_guard_refuses_interactively_when_looper_enabled(self) -> None:
        body = f"""
source {ENGINE_GUARD_SH}
export MPE_LOOPER_ENABLED=1
if mpe_guard_looper_engine test; then exit 9; fi
echo refused
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOOPER-GUARDED", result.stderr)
        self.assertEqual(result.stdout.strip(), "refused")

    def test_guard_allows_when_looper_disabled(self) -> None:
        body = f"""
source {ENGINE_GUARD_SH}
export MPE_LOOPER_ENABLED=0
mpe_guard_looper_engine test
echo allowed
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "allowed")

    def test_python_guard_matches_shell_policy(self) -> None:
        blocked = looper_guard_blocked(looper_enabled="1")
        self.assertTrue(blocked)
        self.assertEqual(looper_guard_exit_code(looper_service=True), 0)
        self.assertEqual(looper_guard_exit_code(looper_service=False), 1)

    def test_looper_guard_message_matches_python(self) -> None:
        body = f"""
source {ENGINE_GUARD_SH}
printf '%s' "$MPE_LOOPER_GUARD_MESSAGE"
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, LOOPER_GUARD_MESSAGE)


class EngineHudReaderTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.state"
            self.assertEqual(read_engine_state(missing), {})
            self.assertFalse(engine_hud_should_show({}))

    def test_partial_file_tolerated(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".state") as fh:
            fh.write("engine=jack\nstate=rec")
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

    def test_recovering_shows_in_label(self) -> None:
        state = {"active": "jack", "state": "recovering"}
        self.assertEqual(engine_hud_label(state), "JACK·rec")

    def test_degraded_no_longer_a_valid_state(self) -> None:
        """degraded is retired — a state file carrying it (e.g. from a pre-upgrade
        appliance) must not be treated as a recognised status in the HUD label."""
        from patch_browser.audio_engine import VALID_ENGINE_STATES

        self.assertNotIn("degraded", VALID_ENGINE_STATES)
        state = {"active": "jack", "state": "degraded"}
        # status not recognised -> no status suffix appended, just the engine name
        self.assertEqual(engine_hud_label(state), "JACK")

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


class GraphRestartSkipTests(unittest.TestCase):
    def test_skip_loopback_card(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
mpe_should_skip_graph_restart_for_card Loopback && echo skip || echo restart
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "skip")

    def test_external_dac_not_skipped(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
mpe_should_skip_graph_restart_for_card Play3 && echo skip || echo restart
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.stdout.strip(), "restart")

    def test_restart_script_filters_skip_cards(self) -> None:
        text = RESTART_AUDIO_GRAPH_SH.read_text(encoding="utf-8")
        self.assertIn("mpe_should_skip_graph_restart_for_card", text)
        self.assertIn("skipped", text)


class JackEngineFailurePublishTests(unittest.TestCase):
    def test_publish_writes_failed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_publish_jack_engine_failure no-server
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = Path(tmp, "engine.state").read_text(encoding="utf-8")
            self.assertIn("state=failed", state)
            self.assertIn("reason=no-server", state)
            self.assertIn("active=none", state)


class JackEnvValidatorTests(unittest.TestCase):
    def test_jack_period_defaults(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
printf '%s' "$(mpe_jack_period)"
"""
        result = _run_bash_script(body, env=_bash_env())
        self.assertEqual(result.stdout.strip(), "256")

    def test_invalid_jack_period_falls_back(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
printf '%s' "$(mpe_jack_period)"
"""
        result = _run_bash_script(body, env=_bash_env(MPE_JACK_BUFFER="9999"))
        self.assertEqual(result.stdout.strip(), "256")
        self.assertIn("WARNING", result.stderr)

    def test_jack_periods_allowlist(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
printf '%s' "$(mpe_jack_periods)"
"""
        result = _run_bash_script(body, env=_bash_env(MPE_JACK_PERIODS="4"))
        self.assertEqual(result.stdout.strip(), "4")

    def test_buffer_env_canonical_uses_jack_key_only(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
printf '%s' "$(mpe_buffer_env_canonical)"
"""
        result = _run_bash_script(
            body,
            env=_bash_env(MPE_JACK_BUFFER="512", MPE_SURGE_BUFFER_SIZE="1024"),
        )
        self.assertEqual(result.stdout.strip(), "512")

    def test_legacy_surge_key_never_sets_the_graph_period(self) -> None:
        """Spec D6: a stale MPE_SURGE_BUFFER_SIZE must not reassign the live period."""
        body = f"""
source {AUDIO_ENGINE_SH}
printf '%s' "$(mpe_buffer_env_canonical)"
"""
        result = _run_bash_script(body, env=_bash_env(MPE_SURGE_BUFFER_SIZE="512"))
        self.assertEqual(result.stdout.strip(), "256")

    def test_export_synced_buffer_env_leaves_surge_key_alone(self) -> None:
        """Writing the keys equal broke MIDI offset — they measure different things."""
        body = f"""
source {AUDIO_ENGINE_SH}
mpe_export_synced_buffer_env
printf '%s %s' "$MPE_JACK_BUFFER" "$MPE_SURGE_BUFFER_SIZE"
"""
        result = _run_bash_script(
            body,
            env=_bash_env(MPE_JACK_BUFFER="512", MPE_SURGE_BUFFER_SIZE="1024"),
        )
        self.assertEqual(result.stdout.strip(), "512 1024")

    def test_export_synced_buffer_env_warns_when_only_legacy_key_set(self) -> None:
        body = f"""
source {AUDIO_ENGINE_SH}
mpe_export_synced_buffer_env
printf '%s' "$MPE_JACK_BUFFER"
"""
        result = _run_bash_script(body, env=_bash_env(MPE_SURGE_BUFFER_SIZE="512"))
        self.assertIn("MPE_JACK_BUFFER is not", result.stderr)
        self.assertEqual(result.stdout.strip(), "256")


class StartJackdPlaybackOnlyTests(unittest.TestCase):
    def test_alsa_backend_opens_playback_only(self) -> None:
        text = START_JACKD_SH.read_text(encoding="utf-8")
        self.assertIn('-d alsa -P "$HW_DEV"', text)
        self.assertNotIn('-d alsa -d "$HW_DEV"', text)


class LooperRestartPrefersTheUnitTests(unittest.TestCase):
    """Two restart paths for one engine is a race for OSC port 9951.

    restart-sooperlooper.sh kills the engine and starts its own. With
    mpe-sooperlooper.service (Restart=always) installed, systemd starts one too — both
    bind 9951, one dies, and which one is a coin flip. A buffer change does four graph
    restarts, so a sweep would roll that dice four times.
    """

    @staticmethod
    def _code_lines() -> list[str]:
        """Function body with comments stripped — comments name both paths."""
        source = AUDIO_ENGINE_SH.read_text(encoding="utf-8")
        body = source.split("mpe_restart_looper_after_graph_change()")[1].split("\n}")[0]
        return [
            line
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_defers_to_the_unit_before_the_script(self) -> None:
        code = self._code_lines()
        unit_check = next(
            i for i, ln in enumerate(code) if "mpe-sooperlooper.service" in ln
        )
        script_use = next(
            i for i, ln in enumerate(code) if "restart-sooperlooper.sh" in ln
        )
        self.assertLess(
            unit_check,
            script_use,
            "the unit branch must come BEFORE falling back to the script",
        )
        self.assertTrue(
            any("restart mpe-sooperlooper.service" in ln for ln in code),
            "the unit branch must actually restart the unit",
        )

    def test_script_fallback_survives_for_unsupervised_installs(self) -> None:
        self.assertTrue(
            any("restart-sooperlooper.sh" in ln for ln in self._code_lines())
        )


class JackSoftmodeTests(unittest.TestCase):
    """Softmode ships on; a bench run must be able to turn it off to name a culprit."""

    def _softmode(self, **env) -> int:
        body = f"""
source {AUDIO_ENGINE_SH}
if mpe_jack_softmode_enabled; then printf 'on'; else printf 'off'; fi
"""
        return _run_bash_script(body, env=_bash_env(**env)).stdout.strip()

    def test_defaults_to_softmode_on(self) -> None:
        self.assertEqual(self._softmode(), "on")

    def test_explicit_zero_disables(self) -> None:
        self.assertEqual(self._softmode(MPE_JACK_SOFTMODE="0"), "off")
        self.assertEqual(self._softmode(MPE_JACK_SOFTMODE="off"), "off")

    def test_garbage_stays_safe(self) -> None:
        self.assertEqual(self._softmode(MPE_JACK_SOFTMODE="banana"), "on")

    def test_start_jackd_passes_the_flag_conditionally(self) -> None:
        text = START_JACKD_SH.read_text(encoding="utf-8")
        self.assertIn("mpe_jack_softmode_enabled", text)
        self.assertIn('"${SOFTMODE_ARGS[@]}"', text)
        self.assertNotIn('jackd -R -P"$JACK_PRIO" -s', text)


class SessionEventBashTests(unittest.TestCase):
    def test_engine_transition_emits_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_engine_state_write jack none recovering boot off
mpe_engine_state_write jack jack ok "" off
mpe_engine_state_write jack none failed no-server off
wc -l < "$(mpe_run_dir)/events.jsonl"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreaterEqual(int(result.stdout.strip()), 2)

    def test_buffer_change_emits_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_jack_state_write hw:0 256 3 48000
mpe_jack_state_write hw:0 128 3 48000
grep -c buffer.changed "$(mpe_run_dir)/events.jsonl"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1")

    def test_engine_exit_reason_with_quotes_survives_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _bash_env(tmp)
            body = f"""
source {AUDIO_ENGINE_SH}
mpe_session_event_emit engine.exited 'surge said "no" / path C:\\x' reason='surge said "no"'
python3 -c "import json; from pathlib import Path; lines=Path('$(mpe_run_dir)/events.jsonl').read_text().splitlines(); obj=json.loads(lines[-1]); assert obj['event']=='engine.exited'; assert 'no' in obj['detail']; assert 'no' in obj['reason']"
"""
            result = _run_bash_script(body, env=env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
