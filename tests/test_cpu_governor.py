"""Tests for CPU governor pinning and Surge realtime scheduling config.

See docs/LATENCY-SPIKE.md (Arm A½) for why these knobs exist.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GOVERNOR_SCRIPT = REPO_ROOT / "scripts" / "set-cpu-governor.sh"


CPU0_CPUFREQ = Path("/sys/devices/system/cpu/cpu0/cpufreq")


def _run_governor(env_value: str | None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    if env_value is not None:
        env["MPE_CPU_GOVERNOR"] = env_value
    return subprocess.run(
        ["bash", str(GOVERNOR_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class CpuGovernorScriptTests(unittest.TestCase):
    def test_noop_when_unset(self) -> None:
        """Existing appliances must be unaffected until the knob is opted into."""
        result = _run_governor(None)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("unset", result.stdout)

    def test_empty_value_is_noop(self) -> None:
        result = _run_governor("")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("unset", result.stdout)

    @unittest.skipUnless(
        CPU0_CPUFREQ.is_dir(),
        "no cpufreq sysfs on this host (e.g. GitHub Actions)",
    )
    def test_unavailable_governor_fails_loudly(self) -> None:
        """A typo must not silently leave the governor unchanged."""
        result = _run_governor("no-such-governor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not available", result.stderr)


class SurgeRealtimeUnitTests(unittest.TestCase):
    """The RT permission is the whole point of Arm A½ — guard it from silent removal."""

    def setUp(self) -> None:
        self.unit = (REPO_ROOT / "config" / "surge-xt-cli.service").read_text(encoding="utf-8")

    def test_unit_permits_realtime_priority(self) -> None:
        self.assertIn("LimitRTPRIO=", self.unit)

    def test_unit_allows_locked_memory(self) -> None:
        self.assertIn("LimitMEMLOCK=", self.unit)

    def test_unit_does_not_force_fifo_on_whole_process(self) -> None:
        """Forcing SCHED_FIFO process-wide can starve the touch UI; opt in via env instead."""
        self.assertNotIn("CPUSchedulingPolicy=", self.unit)

    def test_unit_orders_after_governor(self) -> None:
        self.assertIn("mpe-cpu-governor.service", self.unit)


class GovernorUnitFileTests(unittest.TestCase):
    def test_unit_reads_appliance_env(self) -> None:
        unit = (REPO_ROOT / "config" / "mpe-cpu-governor.service").read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=-/etc/mpe/mpe.env", unit)
        self.assertIn("Before=surge-xt-cli.service", unit)

    def test_env_example_documents_governor_knob(self) -> None:
        example = (REPO_ROOT / "config" / "mpe.env.example").read_text(encoding="utf-8")
        self.assertIn("MPE_CPU_GOVERNOR", example)

    def test_env_example_marks_surge_rt_priority_retired(self) -> None:
        """MPE_SURGE_RT_PRIORITY lost its consumer when PR #50 removed the ALSA
        launch path. The example must not present it as a live knob again."""
        example = (REPO_ROOT / "config" / "mpe.env.example").read_text(encoding="utf-8")
        self.assertIn("MPE_SURGE_RT_PRIORITY", example)
        self.assertIn("RETIRED", example)
        self.assertNotIn("# MPE_SURGE_RT_PRIORITY=", example)


if __name__ == "__main__":
    unittest.main()
