"""The watchdog leaves Surge alone while calibration holds the maintenance flag.

Calibration stops the production Surge and runs its own on the ALSA loopback.
Until 2026-10-22 the watchdog restarted the production Surge within seconds
("promote-to-jack"), putting two Surges on OSC 53280: patch loads and their
confirmations reached the restarted one while the capture heard the other, so
every patch after the first read the same level.

The flag is written by Python (session_snapshot.set_maintenance_flag) and read
by bash (audio-engine.sh mpe_maintenance_active), so these tests write it with
the real writer and check both readers agree.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.session_snapshot import maintenance_mode_active, set_maintenance_flag
from tests.test_audio_engine import AUDIO_ENGINE_SH, SURGE_WATCHDOG_SH, _bash_env, _run_bash_script


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _shell_says_active(run_dir: str) -> bool:
    result = _run_bash_script(
        f"source {AUDIO_ENGINE_SH}\nif mpe_maintenance_active; then echo yes; else echo no; fi\n",
        env=_bash_env(run_dir),
    )
    if result.returncode != 0 or result.stderr:
        raise AssertionError(result.stderr)
    return result.stdout.strip() == "yes"


class MaintenanceFlagReader(unittest.TestCase):
    def _case(self, *, deadline_s: float, pid: int, want: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            set_maintenance_flag(run=Path(tmp), source="calibration", deadline_s=deadline_s, pid=pid)
            self.assertEqual(_shell_says_active(tmp), want, "bash reader")
            self.assertEqual(maintenance_mode_active(run=Path(tmp)), want, "python reader")

    def test_live_setter_and_future_deadline_is_active(self) -> None:
        self._case(deadline_s=600.0, pid=os.getpid(), want=True)

    def test_lapsed_deadline_is_not_active(self) -> None:
        self._case(deadline_s=-5.0, pid=os.getpid(), want=False)

    def test_dead_setter_is_not_active(self) -> None:
        self._case(deadline_s=600.0, pid=_dead_pid(), want=False)

    def test_no_flag_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_shell_says_active(tmp))

    def test_unparseable_deadline_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "maintenance").write_text(
                f"pid={os.getpid()}\ndeadline=soon\nsource=calibration\n", encoding="utf-8"
            )
            self.assertFalse(_shell_says_active(tmp))


class WatchdogUnderMaintenance(unittest.TestCase):
    STUBS = """
mpe_surge_on_jack_graph() { echo GRAPH_PROBE >&2; return 1; }
mpe_jack_server_ready() { return 0; }
mpe_systemctl() { echo "SYSTEMCTL:$*" >&2; return 0; }
export -f mpe_systemctl
"""

    def _reconcile(self, *, flag: bool) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            if flag:
                set_maintenance_flag(run=Path(tmp), deadline_s=600.0, pid=os.getpid())
            env = _bash_env(tmp, MPE_WATCHDOG_LOG=f"{tmp}/watchdog.log")
            return _run_bash_script(
                f"source {SURGE_WATCHDOG_SH}\n{self.STUBS}\n_reconcile_engine\n", env=env
            )

    def test_off_graph_surge_is_promoted_without_the_flag(self) -> None:
        result = self._reconcile(flag=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYSTEMCTL:restart surge-xt-cli.service", result.stderr)

    def test_no_restart_while_calibration_holds_the_flag(self) -> None:
        result = self._reconcile(flag=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SYSTEMCTL:", result.stderr)
        self.assertNotIn("GRAPH_PROBE", result.stderr, "probes the graph it must leave alone")

    def test_failed_unit_restart_also_waits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            set_maintenance_flag(run=Path(tmp), deadline_s=600.0, pid=os.getpid())
            env = _bash_env(tmp, MPE_WATCHDOG_LOG=f"{tmp}/watchdog.log")
            result = _run_bash_script(
                f"source {SURGE_WATCHDOG_SH}\n{self.STUBS}\n"
                "if _supervisor_restart_surge surge-failed; then exit 9; fi\necho deferred\n",
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "deferred")
            self.assertNotIn("SYSTEMCTL:", result.stderr)


class CalibrationKeepsTheFlagAlive(unittest.TestCase):
    def test_refresh_pushes_the_deadline_out(self) -> None:
        from patch_browser import calibration_teardown as ct

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            set_maintenance_flag(run=run, deadline_s=1.0, pid=os.getpid())
            with mock.patch.object(
                ct, "set_maintenance_flag", lambda **kw: set_maintenance_flag(run=run, **kw)
            ):
                ct.refresh_maintenance_flag()
            text = (run / "maintenance").read_text(encoding="utf-8")
            deadline = float(text.split("deadline=")[1].split()[0])
            self.assertGreater(deadline, time.time() + 600)
            self.assertIn(f"pid={os.getpid()}", text)


class TeardownKillsOnlySurge(unittest.TestCase):
    def test_pkill_matches_the_process_name_not_command_lines(self) -> None:
        """-f also matched any shell whose command line named surge-xt-cli."""
        from patch_browser import calibration_teardown as ct

        with mock.patch.object(ct.subprocess, "run") as run, \
                mock.patch.object(ct, "unload_snd_aloop_if_idle"), \
                mock.patch.object(ct, "_systemctl"), mock.patch.object(ct, "_safe_emit_event"), \
                mock.patch.object(ct, "clear_maintenance_flag"), mock.patch.object(ct.time, "sleep"):
            ct.restore_mpe_audio_services(restart_browser=False)
        kills = [c.args[0] for c in run.call_args_list if "pkill" in c.args[0]]
        self.assertEqual(kills, [["sudo", "pkill", "-x", "surge-xt-cli"]])


class CalibratorRefreshesPerPatch(unittest.TestCase):
    """main() refreshes the flag once per patch whenever it took the services down."""

    def _run_main(self, *, handoff: bool, patches: int) -> mock.Mock:
        from tests.test_calibrate_midi import load_cal_module

        cal = load_cal_module()
        paths = [Path(f"/tmp/Fake{i}.fxp") for i in range(patches)]
        with (
            mock.patch.object(cal, "parse_args") as parse_args,
            mock.patch.object(cal, "collect_patch_paths", return_value=paths),
            mock.patch.object(cal, "PatchNormalizationStore") as store_cls,
            mock.patch.object(cal, "PatchLoader") as loader_cls,
            mock.patch.object(cal, "wait_for_surge_midi_port", return_value=0),
            mock.patch.object(cal, "should_use_loopback", return_value=False),
            mock.patch.object(cal, "should_restart_surge_for_standalone", return_value=handoff),
            mock.patch.object(cal, "calibration_audio_scope", lambda **kw: contextlib.nullcontext()),
            mock.patch.object(cal, "start_surge_standalone", return_value="0.0"),
            mock.patch.object(cal, "detect_capture_device", return_value="plughw:Loopback,1,0"),
            mock.patch.object(cal, "emit_progress"),
            mock.patch.object(cal, "open_midi_out", return_value=mock.Mock()),
            mock.patch.object(cal, "close_midi_out"),
            mock.patch.object(cal, "calibrate_patch", return_value=cal.CalibrateResult(ok=True)),
            mock.patch.object(cal, "backup_store", return_value=None),
            mock.patch.object(cal, "refresh_maintenance_flag") as refresh,
        ):
            store_cls.return_value.list_missing.return_value = [p.stem for p in paths]
            loader_cls.return_value.osc_enabled = True
            parse_args.return_value = mock.Mock(
                output=Path("/tmp/test-patch-normalization.json"), pressure_output=None,
                use_loopback=False, audio_device="plughw:Loopback,1,0", favorites_only=False,
                folder=None, limit=0, force=True, verify_only=False, audit=None, controls=None,
                no_touch_cal=True, patch=None, mock_lufs=None, dry_run=False,
                progress_json=False, no_restore_services=True, osc_host="127.0.0.1",
                osc_port=53280,
            )
            cal.main()
        return refresh

    def test_once_per_patch_under_handoff(self) -> None:
        self.assertEqual(self._run_main(handoff=True, patches=3).call_count, 3)

    def test_not_without_handoff(self) -> None:
        self.assertEqual(self._run_main(handoff=False, patches=3).call_count, 0)


if __name__ == "__main__":
    unittest.main()
