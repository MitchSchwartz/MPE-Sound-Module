"""jackd's period floor on the snd-aloop idle sink.

MEASURED on the appliance 2026-08-30, three attempts each, waiting 7s for the
driver thread to come up:

    jackd -d alsa -P hw:7 -p 64   -> "Driver is not running", every attempt;
                                     the server stays up but no client attaches
    jackd -d alsa -P hw:7 -p 128  -> driver runs, clients attach
    jackd -d alsa -P hw:7 -p 192  -> driver runs, clients attach

The appliance runs MPE_JACK_BUFFER=64, so on a Pi 5 with no DAC the idle graph
came up with a live jackd and zero reachable clients -- Surge and SooperLooper
both failed with "cannot connect to jack" against a server that systemd
reported active.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "start-jackd.sh"


class IdleSinkPeriodFloorTests(unittest.TestCase):
    def _launch(self, card_id, period):
        """Run start-jackd.sh with a fake jackd; return the argv it exec'd."""
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        argv_log = tmp / "argv"
        fake = bin_dir / "jackd"
        fake.write_text(f'#!/bin/bash\necho "$*" > {argv_log}\n')
        fake.chmod(0o755)

        device_file = tmp / "jack-device"
        device_file.write_text(f"JACK_DEVICE=hw:7\nJACK_CARD_ID={card_id}\n")

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "HOME": str(tmp),
                "MPE_JACK_DEVICE_FILE": str(device_file),
                "MPE_RUN_DIR": str(tmp / "run"),
                "MPE_JACK_BUFFER": str(period),
            }
        )
        (tmp / "run").mkdir()
        proc = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
        )
        self.assertTrue(argv_log.exists(), f"jackd never ran: {proc.stderr}")
        return argv_log.read_text(), proc.stderr

    def test_the_loopback_is_raised_off_the_period_it_cannot_run(self):
        argv, stderr = self._launch("Loopback", 64)
        self.assertIn("-p 128", argv)
        self.assertNotIn("-p 64", argv)
        self.assertIn("raising to 128", stderr, "a silent latency change")

    def test_a_real_dac_keeps_the_configured_period(self):
        """Positive control. A floor applied everywhere would double the
        period on the Scarlett too, which is the player's actual latency."""
        argv, _ = self._launch("USB", 64)
        self.assertIn("-p 64", argv)

    def test_a_loopback_already_above_the_floor_is_untouched(self):
        argv, _ = self._launch("Loopback", 256)
        self.assertIn("-p 256", argv)


if __name__ == "__main__":
    unittest.main()
