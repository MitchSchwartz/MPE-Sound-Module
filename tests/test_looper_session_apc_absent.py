"""No APC means no looper session and no restart loop (#154, 2026-10-11).

THE FAILURE. The APC is optional hardware, and mpe-looper-session exists to drive
it. With no APC the session printed "No APC port", exited 1, and Restart=always
started it again 10 s later. On the appliance that happened 3535 times. Each
start opened the OSC session, a HUD thread and its jack_cpu_load JACK client,
then tore them down about 0.3 s later. 9 of 11 jackd xruns logged that evening
fell inside one of those 0.3 s lifetimes, which cover 2.6% of wall time. Each
of those xruns cut the governor's poly to 3.

THE FIX follows the repo's existing pattern for optional hardware (the foot
pedal):

* udev starts the unit when an APC card appears and stops it on removal
  (config/99-apc-looper.rules). The unit has no [Install] section.
* The session checks for the card before it opens anything. With no card it
  exits APC_ABSENT_EXIT, and the unit refuses to restart on that status.
* surge-watchdog's 30 s reconcile does not start the session without a card,
  and does not fork its python reconciler because the session is idle.

Each test below fails if its half of that is undone. The mutation checks are
recorded in the PR.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import apc_presence as ap
from patch_browser import calibration_teardown as ct

REPO = Path(__file__).resolve().parents[1]
SOOPER = REPO / "scripts" / "sooperlooper"
UNIT = REPO / "config" / "mpe-looper-session.service"
RULES = REPO / "config" / "99-apc-looper.rules"
WATCHDOG = REPO / "scripts" / "surge-watchdog.sh"
RESTART = REPO / "scripts" / "restart-looper-session.sh"

# /proc/asound/cards from the appliance on 2026-10-11, with no APC attached.
CARDS_NO_APC = """\
 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 3 [KA1            ]: USB-Audio - FiiO KA1
                      FiiO FiiO KA1 at usb-xhci-hcd.1-1.1.3, high speed
 4 [VI61           ]: USB-Audio - VI61
                      Alesis VI61 at usb-xhci-hcd.1-1.2, full speed
"""
CARDS_WITH_APC = CARDS_NO_APC + """\
 5 [mini           ]: USB-Audio - APC MINI
                      Akai Professional APC MINI at usb-xhci-hcd.1-1.4, full speed
"""


def _directive(text: str, key: str) -> list[str]:
    return re.findall(rf"^{re.escape(key)}=(.*)$", text, re.M)


def _rule_lines() -> list[str]:
    return [
        ln for ln in RULES.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


class ApcPresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _cards(self, text: str) -> Path:
        p = self.tmp / "cards"
        p.write_text(text, encoding="utf-8")
        return p

    def test_absent_on_the_appliance_card_list(self) -> None:
        self.assertIs(ap.apc_present(cards_file=self._cards(CARDS_NO_APC), hint="APC"), False)

    def test_present_when_an_apc_card_is_listed(self) -> None:
        self.assertIs(ap.apc_present(cards_file=self._cards(CARDS_WITH_APC), hint="APC"), True)

    def test_mk2_spelling_matches(self) -> None:
        text = CARDS_NO_APC + " 5 [mk2 ]: USB-Audio - APC mini mk2\n"
        self.assertIs(ap.apc_present(cards_file=self._cards(text), hint="APC"), True)

    def test_unreadable_card_list_is_unknown_not_absent(self) -> None:
        """An unreadable /proc must not read as "absent". That would switch the pads off silently."""
        self.assertIsNone(ap.apc_present(cards_file=self.tmp / "missing", hint="APC"))

    def test_hint_follows_the_bench_env_var(self) -> None:
        with mock.patch.dict(os.environ, {"MPE_APC_MIDI_PORT": "VI61"}):
            self.assertIs(ap.apc_present(cards_file=self._cards(CARDS_NO_APC)), True)
        with mock.patch.dict(os.environ, {"MPE_APC_MIDI_PORT": ""}):
            self.assertEqual(ap.port_hint(), "APC")

    def test_wait_is_bounded_and_gives_up(self) -> None:
        clock = [0.0]
        sleeps: list[float] = []

        def _sleep(s: float) -> None:
            sleeps.append(s)
            clock[0] += s

        got = ap.wait_for_apc(
            timeout_s=3.0, step_s=0.25, cards_file=self._cards(CARDS_NO_APC), hint="APC",
            sleep=_sleep, monotonic=lambda: clock[0],
        )
        self.assertIs(got, False)
        self.assertLessEqual(sum(sleeps), 3.0 + 0.25)
        self.assertEqual(len(sleeps), 12)

    def test_wait_returns_as_soon_as_the_card_appears(self) -> None:
        path = self._cards(CARDS_NO_APC)
        calls = []

        def _sleep(_s: float) -> None:
            calls.append(1)
            path.write_text(CARDS_WITH_APC, encoding="utf-8")

        got = ap.wait_for_apc(timeout_s=3.0, cards_file=path, hint="APC",
                              sleep=_sleep, monotonic=lambda: 0.0)
        self.assertIs(got, True)
        self.assertEqual(len(calls), 1)


def _run_session(presence: str, argv: list[str]) -> subprocess.CompletedProcess:
    """Run the real run_session with fakes. The fakes record what was opened."""
    body = f"""
import sys
sys.path.insert(0, {str(SOOPER)!r})
sys.path.insert(0, {str(REPO)!r})
import looper_session as ls
opened = []
ls.wait_for_apc = lambda *a, **k: {presence}
class _FakeSession:
    def __init__(self):
        opened.append("osc")
    def start(self):
        return self
    def register_hud_loops(self):
        pass
ls.SlOscSession = _FakeSession
def _hud(_s):
    opened.append("hud")
    raise SystemExit(0)
ls.start_hud_thread = _hud
class _FakeBench:
    @staticmethod
    def run_bench(argv=None, *, osc_session=None):
        opened.append("bench")
        return 0
ls._load_bench_module = lambda: _FakeBench
try:
    rc = ls.run_session({argv!r})
finally:
    print("OPENED=" + ",".join(opened))
raise SystemExit(rc)
"""
    return subprocess.run([sys.executable, "-c", body], capture_output=True, text=True,
                          cwd=REPO, timeout=60)


class SessionExitsCleanWithoutApcTests(unittest.TestCase):
    def test_absent_apc_exits_with_the_no_restart_status(self) -> None:
        r = _run_session("False", [])
        self.assertEqual(r.returncode, ap.APC_ABSENT_EXIT, r.stdout + r.stderr)
        self.assertIn("no APC connected", r.stdout)

    def test_absent_apc_opens_nothing(self) -> None:
        """The restart's cost was the open/close of OSC, HUD and a JACK client. None may happen."""
        r = _run_session("False", [])
        opened = [ln for ln in r.stdout.splitlines() if ln.startswith("OPENED=")]
        self.assertEqual(opened, ["OPENED="], r.stdout + r.stderr)

    def test_bench_only_is_gated_too(self) -> None:
        r = _run_session("False", ["--bench-only"])
        self.assertEqual(r.returncode, ap.APC_ABSENT_EXIT, r.stdout + r.stderr)

    def test_present_apc_runs_the_bench(self) -> None:
        r = _run_session("True", ["--bench-only"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OPENED=osc,bench", r.stdout)

    def test_unreadable_card_list_falls_through_to_the_bench(self) -> None:
        r = _run_session("None", ["--bench-only"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OPENED=osc,bench", r.stdout)

    def test_hud_only_debug_mode_needs_no_apc(self) -> None:
        body = f"""
import sys
sys.path.insert(0, {str(SOOPER)!r})
sys.path.insert(0, {str(REPO)!r})
import looper_session as ls
ls.wait_for_apc = lambda *a, **k: (_ for _ in ()).throw(AssertionError("hud-only checked the APC"))
class _Stop(Exception):
    pass
class _FakeSession:
    def start(self):
        raise _Stop()
ls.SlOscSession = _FakeSession
try:
    ls.run_session(["--hud-only"])
except _Stop:
    print("reached-osc")
"""
        r = subprocess.run([sys.executable, "-c", body], capture_output=True, text=True,
                           cwd=REPO, timeout=60)
        self.assertIn("reached-osc", r.stdout, r.stderr)


class UnitDoesNotRestartOnAbsenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = UNIT.read_text(encoding="utf-8")

    def test_absent_status_prevents_restart(self) -> None:
        self.assertEqual(_directive(self.text, "RestartPreventExitStatus"),
                         [str(ap.APC_ABSENT_EXIT)])

    def test_absent_status_is_a_clean_stop(self) -> None:
        self.assertEqual(_directive(self.text, "SuccessExitStatus"), [str(ap.APC_ABSENT_EXIT)])

    def test_a_crash_with_the_apc_present_still_restarts(self) -> None:
        self.assertEqual(_directive(self.text, "Restart"), ["always"])

    def test_not_started_at_boot_by_an_install_target(self) -> None:
        """udev owns the start. An [Install] target would start it at boot with no APC."""
        self.assertIsNone(re.search(r"^\[Install\]", self.text, re.M))
        self.assertEqual(_directive(self.text, "WantedBy"), [])


class UdevRuleTests(unittest.TestCase):
    def test_plug_starts_the_session(self) -> None:
        starts = [ln for ln in _rule_lines()
                  if 'SYSTEMD_WANTS}+="mpe-looper-session.service"' in ln]
        self.assertEqual(len(starts), 1, _rule_lines())
        ln = starts[0]
        self.assertIn('ACTION=="change"', ln)
        self.assertIn('KERNEL=="card[0-9]*"', ln)
        self.assertIn('ENV{SOUND_INITIALIZED}=="1"', ln)
        self.assertIn('TAG+="systemd"', ln)

    def test_unplug_stops_the_session(self) -> None:
        stops = [ln for ln in _rule_lines() if 'ACTION=="remove"' in ln]
        self.assertEqual(len(stops), 1, _rule_lines())
        self.assertIn("systemctl --no-block stop mpe-looper-session.service", stops[0])

    def test_both_rules_match_only_an_akai_apc(self) -> None:
        for ln in _rule_lines():
            self.assertIn('ENV{ID_VENDOR_ID}=="09e8"', ln)
            self.assertIn('ENV{ID_MODEL}=="*APC*"', ln)

    def test_rule_is_installed(self) -> None:
        installer = (REPO / "scripts" / "install-udev-rules.sh").read_text(encoding="utf-8")
        self.assertIn("config/99-apc-looper.rules", installer)

    @unittest.skipUnless(shutil.which("udevadm"), "udevadm not available")
    def test_rule_file_parses(self) -> None:
        probe = subprocess.run(["udevadm", "verify", "--help"], capture_output=True)
        if probe.returncode != 0:
            self.skipTest("udevadm verify not available")
        r = subprocess.run(["udevadm", "verify", "--no-style", str(RULES)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ReconcileRespectsAbsenceTests(unittest.TestCase):
    """surge-watchdog's 30 s reconcile must not rebuild the loop at its own period."""

    def _started(self, presence) -> list[str]:
        with mock.patch.object(ct, "maintenance_mode_active", return_value=False), \
             mock.patch.object(ct, "systemd_unit_enabled", return_value=True), \
             mock.patch.object(ct, "systemd_unit_active", return_value=False), \
             mock.patch.object(ct, "apc_present", return_value=presence), \
             mock.patch.object(ct, "_systemctl") as sc:
            ct.ensure_looper_units_running()
        return [c.args[0] for c in sc.call_args_list if c.args[1] == "start"]

    def test_no_apc_no_session_start(self) -> None:
        started = self._started(False)
        self.assertNotIn("mpe-looper-session", started)
        self.assertIn("mpe-sooperlooper", started)
        self.assertIn("sl-watchdog", started)

    def test_apc_present_session_recovered(self) -> None:
        self.assertIn("mpe-looper-session", self._started(True))

    def test_unknown_presence_still_recovers(self) -> None:
        self.assertIn("mpe-looper-session", self._started(None))

    def test_calibration_restore_skips_the_session_without_an_apc(self) -> None:
        with mock.patch.object(ct, "apc_present", return_value=False), \
             mock.patch.object(ct, "clear_maintenance_flag"), \
             mock.patch.object(ct, "emit_event"), \
             mock.patch.object(ct, "unload_snd_aloop_if_idle"), \
             mock.patch.object(ct.time, "sleep"), \
             mock.patch.object(ct.subprocess, "run") as run:
            ct.restore_mpe_audio_services(restart_browser=False)
        started = [list(c.args[0])[3] for c in run.call_args_list
                   if list(c.args[0])[:3] == ["sudo", "systemctl", "start"]]
        self.assertNotIn("mpe-looper-session.service", started)
        self.assertIn("mpe-sooperlooper.service", started)

    def test_watchdog_prefilter_does_not_watch_the_session(self) -> None:
        """An idle session would fork the python reconciler every 30 s, forever."""
        text = WATCHDOG.read_text(encoding="utf-8")
        fn = text.split("_reconcile_looper_units_if_needed() {", 1)[1].split("\n}", 1)[0]
        units = fn.split("units=(", 1)[1].split(")", 1)[0].split()
        self.assertEqual(units, ["mpe-sooperlooper.service", "sl-watchdog.service"])


FAKE_SYSTEMCTL = """#!/usr/bin/env bash
case "$1" in
    cat) exit 0 ;;
    is-active) [ "${ACTIVE:-no}" = yes ] && { echo active; exit 0; }; echo inactive; exit 3 ;;
    show) echo "${EXEC_STATUS:-0}"; exit 0 ;;
    status) exit 3 ;;
esac
exit 0
"""


class RestartScriptTests(unittest.TestCase):
    """A deploy with no APC must not report a failed restart, and must not hide a real one."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        for name, body in {
            "systemctl": FAKE_SYSTEMCTL,
            "sudo": '#!/usr/bin/env bash\nexec "$@"\n',
            "pgrep": "#!/usr/bin/env bash\nexit 1\n",
            "sleep": "#!/usr/bin/env bash\nexit 0\n",
            "journalctl": "#!/usr/bin/env bash\nexit 0\n",
        }.items():
            (bin_ / name).write_text(body)
            (bin_ / name).chmod(0o755)
        self.bin = bin_

    def _run(self, exec_status: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                   EXEC_STATUS=exec_status, ACTIVE="no",
                   MPE_SEQ_CLIENTS=str(self.tmp / "none"))
        return subprocess.run(["bash", str(RESTART)], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_no_apc_is_a_successful_idle_restart(self) -> None:
        r = self._run(str(ap.APC_ABSENT_EXIT))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no APC connected", r.stdout)

    def test_any_other_inactive_status_still_fails(self) -> None:
        r = self._run("1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
