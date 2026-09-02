"""One period list, and a fallback that climbs off a period the DAC cannot run.

THE BUGS (2026-09-01, both measured on the appliance).

1. The list had diverged four ways. 96 and 192 were accepted by the shell
   validator and absent from BOTH patch_browser and mpe-cli, so the appliance
   would run periods no user interface could offer.

2. jackd stays ALIVE when its driver thread fails to start. On the Apple
   full-speed dongle at -p 64 it printed "LockedTimedWait ... / Driver is not
   running", systemd reported the unit active, engine.state read ok, and Surge
   retried forever against a server that could never accept a client. The only
   signal reaching the player was silence.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIO_ENGINE = REPO_ROOT / "scripts" / "lib" / "audio-engine.sh"
PERIODS_CONF = REPO_ROOT / "config" / "jack-periods.conf"


def _sh(snippet: str) -> str:
    proc = subprocess.run(
        ["bash", "-c", f'MPE_MODULE_REPO="{REPO_ROOT}"\nsource "{AUDIO_ENGINE}"\n{snippet}\n'],
        capture_output=True, text=True, timeout=30)
    return proc.stdout.strip()


def _conf_values() -> list[int]:
    out = []
    for line in PERIODS_CONF.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.isdigit():
            out.append(int(line))
    return out


class SingleSourceTests(unittest.TestCase):
    def test_conf_is_the_shell_validator(self):
        shell = [int(x) for x in _sh("mpe_jack_period_presets").split()]
        self.assertEqual(shell, _conf_values())

    def test_python_reads_the_same_list(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from patch_browser.surge_audio import JACK_PERIOD_PRESETS
        self.assertEqual(list(JACK_PERIOD_PRESETS), _conf_values())

    def test_96_and_192_are_reachable(self):
        """The regression: runnable on the appliance, unreachable from any UI."""
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from patch_browser.surge_audio import JACK_PERIOD_PRESETS
        for period in (96, 192):
            self.assertIn(period, JACK_PERIOD_PRESETS)
            self.assertEqual(_sh(f"mpe_jack_period_is_valid {period} && echo yes"), "yes")

    def test_no_script_carries_its_own_copy_of_the_list(self):
        offenders = []
        for path in sorted((REPO_ROOT / "scripts").rglob("*.sh")):
            for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if "1024)" in s and "512" in s and "256" in s and "case" not in s:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{num}: {s}")
        self.assertEqual(offenders, [], "period list must come from jack-periods.conf")

    def test_a_value_outside_the_list_is_refused(self):
        self.assertEqual(_sh("mpe_jack_period_is_valid 768 && echo yes || echo no"), "no")


class FallbackLadderTests(unittest.TestCase):
    def test_ladder_starts_with_what_was_asked_for(self):
        self.assertEqual(_sh("mpe_jack_fallback_ladder 64").split()[0], "64")

    def test_64_climbs_to_128_then_256(self):
        """The measured case: the full-speed dongle cannot run 64, is clean at 128."""
        self.assertEqual(_sh("mpe_jack_fallback_ladder 64").split(), ["64", "128", "256"])

    def test_ladder_never_descends(self):
        """T13 measured that period SIZE binds. Falling to a smaller period is the
        one direction known to be worse, so it must be unreachable."""
        for start in (128, 256, 512, 1024):
            rungs = [int(x) for x in _sh(f"mpe_jack_fallback_ladder {start}").split()]
            self.assertEqual(rungs, sorted(rungs))
            self.assertTrue(all(r >= start for r in rungs), f"descended from {start}")

    def test_no_duplicate_rung_when_configured_is_already_a_fallback(self):
        self.assertEqual(_sh("mpe_jack_fallback_ladder 128").split(), ["128", "256"])

    def test_top_of_range_has_nowhere_to_climb(self):
        self.assertEqual(_sh("mpe_jack_fallback_ladder 1024").split(), ["1024"])

    def test_ladder_rungs_are_all_valid_periods(self):
        for r in _sh("mpe_jack_fallback_ladder 32").split():
            self.assertEqual(_sh(f"mpe_jack_period_is_valid {r} && echo yes"), "yes")


class FallbackIsVisibleTests(unittest.TestCase):
    """A period the player did not choose is latency they cannot account for."""

    @staticmethod
    def _state_after(args: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "jack.state")
            _sh(f'export MPE_JACK_STATE_FILE="{f}" MPE_RUN_DIR="{tmp}"\n'
                f'mpe_jack_state_write {args} >/dev/null 2>&1 || true')
            return f.read_text(encoding="utf-8") if f.exists() else ""

    def test_state_records_what_was_requested(self):
        out = self._state_after("hw:5 128 2 48000 A 2 64")
        self.assertIn("period=128", out)
        self.assertIn("requested_period=64", out)

    def test_requested_defaults_to_applied_when_no_fallback(self):
        out = self._state_after("hw:0 64 2 48000 USB 2")
        self.assertIn("requested_period=64", out)

    def test_start_jackd_warns_by_name_on_fallback(self):
        src = (REPO_ROOT / "scripts" / "start-jackd.sh").read_text(encoding="utf-8")
        self.assertIn("REQUESTED_BUFFER", src)
        self.assertIn("Latency is higher than configured", src)

    def test_start_jackd_probes_for_ports_not_just_a_live_process(self):
        """jackd alive != driver running. That distinction is the whole bug."""
        src = (REPO_ROOT / "scripts" / "start-jackd.sh").read_text(encoding="utf-8")
        self.assertIn("system:playback_", src)


if __name__ == "__main__":
    unittest.main()


class ProbeUsesTheSanctionedHelperTests(unittest.TestCase):
    """The readiness probe already existed; do not hand-roll a second one.

    mpe_wait_for_jack_server / mpe_jack_server_ready carry the "running is not
    the same as accepting clients" logic, and mpe_jack_lsp resolves the binary
    once, wraps it in `timeout`, and drops from root to the graph owner. A bare
    `jack_lsp` call in a script does none of that.

    Separately, d1541d8 measured that registering a JACK client on a 10 s timer
    produced 35 xruns/min — the largest single xrun source on the appliance —
    and moved the watchdog's STEADY-STATE probe to meter.state. A bounded wait
    at startup is still correct; a polling loop in a running graph is not.
    """

    SCRIPT = REPO_ROOT / "scripts" / "start-jackd.sh"

    def test_start_jackd_uses_the_bounded_readiness_wait(self):
        src = self.SCRIPT.read_text(encoding="utf-8")
        self.assertIn("mpe_wait_for_jack_server", src)

    def test_start_jackd_never_calls_jack_lsp_directly(self):
        offenders = []
        for num, line in enumerate(self.SCRIPT.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            # `mpe_jack_lsp` is the wrapper and is fine; a bare `jack_lsp` is not.
            if re.search(r"(?<!mpe_)\bjack_lsp\b", s):
                offenders.append(f"{num}: {s}")
        self.assertEqual(offenders, [],
                         "call mpe_jack_lsp, never jack_lsp directly")

    def test_probe_is_bounded_not_an_unbounded_poll(self):
        src = self.SCRIPT.read_text(encoding="utf-8")
        self.assertIn("MPE_JACK_PROBE_TIMEOUT", src)
        self.assertIn("mpe_jack_ready_timeout", src)
