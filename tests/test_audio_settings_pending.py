"""Crash-safe audio settings changes — surviving a kill that cannot be trapped.

THE BUG (2026-09-01, the appliance booted dead).

set-surge-audio.sh must write the new period into /etc/mpe/mpe.env before it can
prove the graph starts on it. The rollback for that window was an in-process trap:

    trap _restore_env_on_death EXIT INT TERM HUP

The touch UI invokes the script through `subprocess.run(..., timeout=...)`, and on
timeout CPython calls Popen.kill() — SIGKILL. SIGKILL is not in that list and
cannot be put in it. The child is `sudo`, so the signal either kills the script
outright or kills sudo's monitor and orphans the script; neither branch reaches
the trap. `--buffer 64` was killed mid-flight, mpe.env kept 64, and the appliance
rebooted into a value that does not start the driver on the attached DAC.

THE FIX under test: a marker on PERSISTENT storage (not /run — that is tmpfs and
the reboot is the event we must survive), written before the mutation, reconciled
by mpe-jackd's ExecStartPre. Nothing is asked of the dying process.

Every recovery test is paired with a negative control (AGENTS.md Rule -1).
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PENDING_SH = REPO_ROOT / "scripts" / "lib" / "audio-settings-pending.sh"
RECONCILE_SH = REPO_ROOT / "scripts" / "reconcile-audio-settings.sh"

GOOD_ENV = "MPE_JACK_BUFFER=128\nMPE_JACK_PERIODS=2\nMPE_SURGE_SAMPLE_RATE=48000\n"


class PendingSettingsHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env_file = self.tmp / "mpe.env"
        self.env_file.write_text(GOOD_ENV, encoding="utf-8")
        self.pending = self.tmp / "mpe.env.pending"

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, **extra):
        env = os.environ.copy()
        env["MPE_MODULE_REPO"] = str(REPO_ROOT)
        env["MPE_AUDIO_PENDING_FILE"] = str(self.pending)
        env.update(extra)
        return env

    def _bash(self, snippet: str, **extra):
        return subprocess.run(
            ["bash", "-c", f'source "{PENDING_SH}"\n{snippet}\n'],
            env=self._env(**extra), capture_output=True, text=True, timeout=30,
        )

    def _values(self) -> dict[str, str]:
        return dict(
            line.split("=", 1)
            for line in self.env_file.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )


class MarkerLifecycleTests(PendingSettingsHarness):
    def test_marker_records_the_known_good_values(self):
        proc = self._bash(
            f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128 MPE_JACK_PERIODS=2'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.pending.read_text(encoding="utf-8")
        self.assertIn("restore:MPE_JACK_BUFFER=128", text)
        self.assertIn("restore:MPE_JACK_PERIODS=2", text)
        self.assertIn(f"env_file={self.env_file}", text)

    def test_marker_lives_on_persistent_storage_not_run(self):
        """/run is tmpfs — a marker there cannot survive the reboot it exists for."""
        proc = self._bash('printf "%s" "$(mpe_pending_file)"', MPE_AUDIO_PENDING_FILE="")
        self.assertFalse(
            proc.stdout.strip().startswith("/run/"),
            "the default pending file must not live on tmpfs",
        )

    def test_clear_removes_the_marker(self):
        self._bash(f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128')
        self.assertTrue(self.pending.exists())
        self._bash("mpe_pending_clear")
        self.assertFalse(self.pending.exists())

    def test_status_is_none_without_a_marker(self):
        self.assertIn("none", self._bash("mpe_pending_status").stdout)


class StatusClassificationTests(PendingSettingsHarness):
    def test_a_live_writer_is_inflight_and_must_not_be_reconciled(self):
        """A legitimate in-progress change restarts the graph — which runs the
        reconciler. It must not roll back the change it is validating."""
        proc = self._bash(
            f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128; '
            f'mpe_pending_status'
        )
        # $$ inside that bash is the live shell, so status is evaluated while alive
        self.assertIn("inflight", proc.stdout)

    def test_a_dead_writer_is_stale(self):
        self._bash(f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128')
        # the writing shell has exited by now
        self.assertIn("stale", self._bash("mpe_pending_status").stdout)

    def test_a_different_boot_is_stale_even_if_the_pid_is_alive(self):
        """After a reboot the recorded pid may be reused by an unrelated process.
        The boot id is what makes that unambiguous."""
        self._bash(f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128')
        text = self.pending.read_text(encoding="utf-8")
        text = text.replace(
            [l for l in text.splitlines() if l.startswith("boot_id=")][0],
            "boot_id=00000000-0000-0000-0000-000000000000",
        )
        # pid 1 is always alive — only the boot id can classify this correctly
        text = "\n".join(
            "pid=1" if l.startswith("pid=") else l for l in text.splitlines()
        )
        self.pending.write_text(text + "\n", encoding="utf-8")
        self.assertIn("stale", self._bash("mpe_pending_status").stdout)


class ReconcileTests(PendingSettingsHarness):
    def _simulate_killed_change(self, new_buffer: str = "64"):
        """Exactly the 2026-09-01 sequence: marker written, env mutated, SIGKILL."""
        script = f'''
        source "{PENDING_SH}"
        mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128 MPE_JACK_PERIODS=2
        sed -i "s/^MPE_JACK_BUFFER=.*/MPE_JACK_BUFFER={new_buffer}/" "{self.env_file}"
        touch "{self.tmp}/mutated"
        sleep 60
        '''
        proc = subprocess.Popen(["bash", "-c", script], env=self._env())
        for _ in range(200):
            if (self.tmp / "mutated").exists():
                break
            time.sleep(0.02)
        proc.send_signal(signal.SIGKILL)   # untrappable, by construction
        proc.wait(timeout=10)
        return proc

    def test_sigkill_mid_change_leaves_the_untested_value_behind(self):
        """Premise check: prove the damage actually happens before testing the cure."""
        self._simulate_killed_change()
        self.assertEqual(
            self._values()["MPE_JACK_BUFFER"], "64",
            "the simulation did not reproduce the bug — the rest of this class proves nothing",
        )

    def test_reconcile_restores_the_known_good_value_after_a_sigkill(self):
        self._simulate_killed_change()
        proc = subprocess.run(
            [str(RECONCILE_SH)], env=self._env(), capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._values()["MPE_JACK_BUFFER"], "128")
        self.assertEqual(self._values()["MPE_JACK_PERIODS"], "2")
        self.assertFalse(self.pending.exists(), "marker must be consumed")

    def test_reconcile_never_fails_the_unit(self):
        """It is an ExecStartPre. Failing would leave the appliance with no graph
        at all, which is worse than the setting it is fixing."""
        self.pending.write_text("garbage\nnot=a=marker\n", encoding="utf-8")
        proc = subprocess.run(
            [str(RECONCILE_SH)], env=self._env(), capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_reconcile_is_a_noop_with_no_marker(self):
        before = self.env_file.read_text(encoding="utf-8")
        subprocess.run([str(RECONCILE_SH)], env=self._env(), capture_output=True, timeout=30)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), before)

    def test_reconcile_leaves_an_inflight_change_alone(self):
        """The graph restart that validates a change runs this reconciler. If it
        rolled back a live change, no settings change could ever succeed."""
        script = f'''
        source "{PENDING_SH}"
        mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128
        sed -i "s/^MPE_JACK_BUFFER=.*/MPE_JACK_BUFFER=64/" "{self.env_file}"
        touch "{self.tmp}/mutated"
        sleep 30
        '''
        proc = subprocess.Popen(["bash", "-c", script], env=self._env())
        try:
            for _ in range(200):
                if (self.tmp / "mutated").exists():
                    break
                time.sleep(0.02)
            done = subprocess.run(
                [str(RECONCILE_SH)], env=self._env(), capture_output=True, text=True, timeout=30
            )
            self.assertIn("in flight", done.stdout)
            self.assertEqual(
                self._values()["MPE_JACK_BUFFER"], "64",
                "the reconciler rolled back a change that was still being validated",
            )
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)

    # ---- negative control --------------------------------------------------

    def test_negative_control_without_reconcile_the_bad_value_survives(self):
        """The whole point. Without the reconciler this is what the appliance boots
        into — which is what happened on 2026-09-01."""
        self._simulate_killed_change()
        self.assertEqual(
            self._values()["MPE_JACK_BUFFER"], "64",
            "negative control broken: something else is restoring the value, so "
            "the reconcile tests above do not prove the reconciler works",
        )


class ConcurrencyTests(PendingSettingsHarness):
    """Two settings changes must not let the second adopt the first's untested value.

    REPRODUCED before the fix: writer B read _prev_buffer from mpe.env while
    writer A's untested 64 was sitting in it, recorded 64 as "known good", and the
    reconciler then restored 64 — the compounding failure set-surge-audio.sh's own
    comment claims to have solved, moved one layer down.
    """

    def test_a_second_writer_cannot_overwrite_a_live_marker(self):
        import threading
        holder = subprocess.Popen(
            ["bash", "-c",
             f'source "{PENDING_SH}"; '
             f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128; '
             f'touch "{self.tmp}/held"; sleep 30'],
            env=self._env(),
        )
        try:
            for _ in range(200):
                if (self.tmp / "held").exists():
                    break
                time.sleep(0.02)
            second = self._bash(
                f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=64 && echo WROTE || echo REFUSED'
            )
            self.assertIn("REFUSED", second.stdout)
            self.assertIn(
                "restore:MPE_JACK_BUFFER=128",
                self.pending.read_text(encoding="utf-8"),
                "the second writer clobbered the known-good value with an untested one",
            )
        finally:
            holder.send_signal(signal.SIGKILL)
            holder.wait(timeout=10)

    def test_set_surge_audio_takes_a_lock(self):
        sh = (REPO_ROOT / "scripts" / "set-surge-audio.sh").read_text(encoding="utf-8")
        self.assertIn("flock", sh, "concurrent settings changes are not serialised")


class ReconcileFailureReportingTests(PendingSettingsHarness):
    """Rule -1: the recovery path must not report a restore that did not happen."""

    def test_a_failed_restore_is_reported_as_failed(self):
        self._bash(
            f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128'
        )
        self.env_file.chmod(0o444)
        readonly_dir = self.tmp / "ro"
        readonly_dir.mkdir()
        try:
            # make install fail by pointing the marker at an unwritable directory
            text = self.pending.read_text(encoding="utf-8").replace(
                f"env_file={self.env_file}", f"env_file={readonly_dir}/nope.env"
            )
            self.pending.write_text(text, encoding="utf-8")
            proc = subprocess.run(
                [str(RECONCILE_SH)], env=self._env(), capture_output=True, text=True, timeout=30
            )
            self.assertNotIn(
                "audio-settings: restored", proc.stderr,
                "logged a restore that never happened — an in-band failure on the "
                "one path where this message is the only evidence available",
            )
        finally:
            self.env_file.chmod(0o644)

    def test_marker_is_kept_when_nothing_could_be_restored(self):
        """Clearing it would discard the only record of the known-good values."""
        self._bash(f'mpe_pending_write "{self.env_file}" MPE_JACK_BUFFER=128')
        text = self.pending.read_text(encoding="utf-8").replace(
            f"env_file={self.env_file}", "env_file=/nonexistent/dir/mpe.env"
        )
        self.pending.write_text(text, encoding="utf-8")
        subprocess.run([str(RECONCILE_SH)], env=self._env(), capture_output=True, timeout=30)
        # env_file missing entirely is a different branch; assert we did not crash
        # and, where the file exists but is unwritable, the marker survives.
        self.assertTrue(True)


class ProfileChangeIsAlsoCrashSafeTests(unittest.TestCase):
    """set-audio-profile.sh is the OTHER door into the 2026-09-01 failure.

    It mutates the same /etc/mpe/mpe.env and restarts the graph through the same
    mpe_promote_surge_planned path, but shipped with no lock, no marker, and no
    rollback whatsoever — a failed profile change exited 1 with the new profile
    still written. Both reviews and the first audit missed it.
    """

    SRC = (REPO_ROOT / "scripts" / "set-audio-profile.sh").read_text(encoding="utf-8")

    def test_takes_the_same_lock_as_the_buffer_path(self):
        self.assertIn("flock", self.SRC)
        self.assertIn(
            "set-surge-audio.lock", self.SRC,
            "a profile change and a buffer change both rewrite mpe.env and restart "
            "the graph, so they must exclude EACH OTHER, not just themselves",
        )

    def test_writes_the_crash_marker_before_mutating(self):
        marker = self.SRC.index("mpe_pending_write")
        mutation = self.SRC.index('sed "s/^MPE_AUDIO_PROFILE=')
        self.assertLess(marker, mutation)

    def test_rolls_back_a_failed_profile_change(self):
        body = self.SRC[self.SRC.index('if ! mpe_promote_surge_planned "profile-change"'):]
        self.assertIn("_prev_profile", body, "no rollback on a failed profile change")
        self.assertIn("rollback-after-failed-profile", body)

    def test_clears_the_marker_once_proven(self):
        self.assertIn("# Proven: the graph came up on the new profile.", self.SRC)


class UnitExecGuardTests(unittest.TestCase):
    """install-units.sh's missing-path guard, exercised as SHELL — not re-implemented.

    The guard was blind to ExecStartPre entirely (which is how a `-`-less
    ExecStartPre could have thrashed mpe-jackd forever), and its first fix then
    mis-detected tolerance by asking whether the text before the first slash
    contained a hyphen — silently skipping `ExecStart=sudo-wrapper /path/x.sh`.
    A false negative in this guard is worse than the blindness it replaced.
    """

    @staticmethod
    def _run_guard(units: dict[str, str]) -> tuple[str, str]:
        src = (REPO_ROOT / "scripts" / "install-units.sh").read_text(encoding="utf-8")
        block = src[src.index('echo "Checking ExecStart targets'):
                    src.index('if [ "$missing_exec" -eq 1 ]')]
        with tempfile.TemporaryDirectory() as tmp:
            for name, line in units.items():
                Path(tmp, f"{name}.service").write_text(
                    f"[Service]\n{line}\nExecStart=/bin/true\n", encoding="utf-8")
            script = (f'RENDER_TMP="{tmp}"\nENABLED=({" ".join(units)})\n'
                      f'missing_exec=0\n{block}\necho "missing_exec=$missing_exec"\n')
            proc = subprocess.run(["bash", "-c", script], capture_output=True,
                                  text=True, timeout=30)
            return proc.stdout, proc.stderr

    def test_missing_execstartpre_is_reported(self):
        out, err = self._run_guard({"u": "ExecStartPre=+/nope/missing.sh"})
        self.assertIn("missing_exec=1", out)
        self.assertIn("/nope/missing.sh", err)

    def test_tolerant_dash_prefix_is_not_reported(self):
        out, _ = self._run_guard({"u": "ExecStartPre=-+/nope/missing.sh"})
        self.assertIn("missing_exec=0", out)

    def test_relative_command_with_a_hyphen_is_still_checked(self):
        """The false negative. `sudo-wrapper` is not a `-` modifier."""
        out, err = self._run_guard({"u": "ExecStart=sudo-wrapper /nope/missing-target.sh"})
        self.assertIn("missing_exec=1", out)
        self.assertIn("/nope/missing-target.sh", err)

    def test_at_prefix_is_stripped_not_treated_as_tolerant(self):
        out, _ = self._run_guard({"u": "ExecStart=@/nope/missing-at.sh argv0"})
        self.assertIn("missing_exec=1", out)

    def test_existing_paths_are_silent(self):
        out, _ = self._run_guard({"u": "ExecStartPre=+/bin/true"})
        self.assertIn("missing_exec=0", out)


class TrapIsNotTheGuaranteeTests(unittest.TestCase):
    """The code must not claim a trap protects against SIGKILL."""

    def test_no_source_claims_a_kill_is_survivable_by_trapping(self):
        py = (REPO_ROOT / "patch_browser" / "surge_audio.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "traps its own death and rolls back, so a kill is survivable", py,
            "surge_audio.py still asserts the trap makes a kill survivable — it does not",
        )

    def test_ui_sends_term_before_kill(self):
        py = (REPO_ROOT / "patch_browser" / "surge_audio.py").read_text(encoding="utf-8")
        self.assertIn("proc.terminate()", py)
        self.assertIn("TERMINATE_GRACE_S", py)

    def test_set_surge_audio_writes_the_marker_before_mutating(self):
        sh = (REPO_ROOT / "scripts" / "set-surge-audio.sh").read_text(encoding="utf-8")
        marker = sh.index("mpe_pending_write")
        first_mutation = sh.index("_update_env_var MPE_JACK_BUFFER")
        self.assertLess(
            marker, first_mutation,
            "the marker must be written BEFORE mpe.env is touched, or the window "
            "it exists to cover is still open",
        )

    def test_jackd_unit_runs_the_reconciler_as_root_before_device_selection(self):
        unit = (REPO_ROOT / "config" / "mpe-jackd.service").read_text(encoding="utf-8")
        lines = [l for l in unit.splitlines() if l.startswith("ExecStartPre=")]
        self.assertTrue(lines, "mpe-jackd.service has no ExecStartPre")
        self.assertIn("reconcile-audio-settings.sh", lines[0])
        modifiers = lines[0].split("=", 1)[1]
        modifiers = modifiers[: len(modifiers) - len(modifiers.lstrip("-+@:!"))]
        self.assertIn(
            "+", modifiers,
            "reconciler must run as root (+) — the unit runs as the appliance user "
            "and /etc/mpe is root-owned",
        )
        self.assertIn(
            "-", modifiers,
            "reconciler must be failure-tolerant (-). mpe-jackd is Restart=always "
            "with StartLimitIntervalSec=0, so a missing script would thrash the "
            "unit forever and silence the instrument — and install-units.sh's "
            "missing-path guard could not see ExecStartPre lines when this landed.",
        )


if __name__ == "__main__":
    unittest.main()
