"""mpe-peak-meter must be brought back whenever the graph is restarted.

The unit is `PartOf=mpe-jackd.service`. PartOf propagates BOTH stop and restart,
so an ordinary `systemctl restart mpe-jackd` recovers the meter by itself, and
every lifecycle call in audio-engine.sh uses restart. Those paths are fine.

The hole is a jackd STOP: a stop takes the meter down and a later start of
jackd starts nothing that is PartOf it, so the unit sits inactive with
Result=success and ExecMainStatus=0, looking entirely healthy. (A jackd crash or
kill was the other half until 2026-10-11, when the client stopped exiting 0 on
JACK's shutdown callback — see `test_jack_shutdown_is_a_failure_exit` below.)

The symptom is an output meter reading zero forever, which is exactly what
genuine silence looks like. Mitch found it by noticing the meter was off
(2026-09-02); nothing in the system reported it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVICES_LIB = REPO / "scripts" / "lib" / "mpe-services.sh"
METER_UNIT = REPO / "config" / "mpe-peak-meter.service"


class PeakMeterRestartTests(unittest.TestCase):
    def test_restart_core_services_brings_the_meter_back(self):
        body = SERVICES_LIB.read_text(encoding="utf-8")
        match = re.search(
            r"mpe_restart_core_services\(\)\s*\{(.*?)\n\}", body, re.S
        )
        self.assertIsNotNone(match, "mpe_restart_core_services not found")
        self.assertIn(
            "mpe_restart_peak_meter",
            match.group(1),
            "restarting the core services must also bring the peak meter back — "
            "PartOf stops it and never starts it",
        )

    def test_the_meter_is_started_not_restarted(self):
        """`restart` on a disabled opt-in unit would defeat the opt-in, and
        `start` on a running unit is a harmless no-op."""
        body = SERVICES_LIB.read_text(encoding="utf-8")
        match = re.search(r"mpe_restart_peak_meter\(\)\s*\{(.*?)\n\}", body, re.S)
        self.assertIsNotNone(match, "mpe_restart_peak_meter not found")
        fn = match.group(1)
        self.assertIn("is-enabled", fn, "must respect the opt-in gate")
        self.assertIn("systemctl start mpe-peak-meter", fn)
        self.assertNotIn("systemctl restart mpe-peak-meter", fn)

    def test_the_unit_is_still_partof_jackd_so_this_guard_is_needed(self):
        """If PartOf is ever dropped the meter would survive on its own and this
        guard becomes redundant rather than wrong — assert the premise holds so
        the reasoning above stays true."""
        unit = METER_UNIT.read_text(encoding="utf-8")
        self.assertIn("PartOf=mpe-jackd.service", unit)

    def test_jack_shutdown_is_a_failure_exit(self):
        """Both JACK clients that sit on the graph for good must exit non-zero
        when JACK goes away, or Restart=on-failure never brings them back."""
        for rel in ("native/mpe-peak-meter/mpe-peak-meter.c",
                    "native/mpe-live-monitor/mpe-live-monitor.c"):
            with self.subTest(client=rel):
                src = (REPO / rel).read_text(encoding="utf-8")
                tail = src[src.rindex("int main("):]
                match = re.search(r"if \(g_jack_shutdown\) \{(.*?)\}", tail, re.S)
                self.assertIsNotNone(match, f"{rel}: no exit branch on JACK shutdown")
                self.assertRegex(match.group(1), r"return [1-9]\d*;")
                self.assertNotIn("(void)g_jack_shutdown", tail)

    def test_the_measurement_harness_restores_the_meter_too(self):
        """The Phase 2 loopback script stops the graph outright. It left the
        meter dead on 2026-09-02."""
        script = (REPO / "scripts" / "measure-dac-loopback.sh").read_text(encoding="utf-8")
        self.assertIn("mpe-peak-meter", script)


if __name__ == "__main__":
    unittest.main()
