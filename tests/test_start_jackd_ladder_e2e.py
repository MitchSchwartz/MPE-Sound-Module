"""start-jackd.sh's period ladder, exercised end to end against a stubbed jackd.

Real hardware cannot produce this failure on demand: the Scarlett 4i4 runs every
period down to 32, and the DAC that fails at 64 is a dongle that may not be
plugged in. So jackd and jack_lsp are stubbed to reproduce the ONE behaviour
that matters and that no exit code reveals —

    jackd stays alive while its driver thread never starts.

MEASURED 2026-09-01 on the Apple full-speed dongle at -p 64: jackd running,
systemd "active", engine.state "ok", and no client could ever attach.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
START_JACKD = REPO_ROOT / "scripts" / "start-jackd.sh"

# Stays alive forever, like the real thing, and records the period it was asked
# for. Never exits on its own — that is the whole point.
FAKE_JACKD = """#!/bin/bash
period=""
while [ $# -gt 0 ]; do
    case "$1" in -p) period="$2"; shift 2 ;; *) shift ;; esac
done
echo "$period" > "$STUB_DIR/current_period"
echo "creating alsa driver ... period = $period"
sleep 600
"""

# Reports the driver's ports only when the period is one the "device" can run.
FAKE_JACK_LSP = """#!/bin/bash
p="$(cat "$STUB_DIR/current_period" 2>/dev/null || echo 0)"
min="$(cat "$STUB_DIR/min_workable" 2>/dev/null || echo 0)"
if [ "$p" -ge "$min" ] 2>/dev/null; then
    echo "system:playback_1"
    echo "system:playback_2"
    exit 0
fi
echo "Driver is not running" >&2
exit 1
"""


class LadderEndToEndTests(unittest.TestCase):
    def _run(self, configured: int, min_workable: int, timeout: int = 25):
        tmp = tempfile.mkdtemp()
        stub, run_dir = Path(tmp, "stub"), Path(tmp, "run")
        stub.mkdir(); run_dir.mkdir()
        (stub / "min_workable").write_text(str(min_workable))

        for name, body in (("jackd", FAKE_JACKD), ("jack_lsp", FAKE_JACK_LSP)):
            p = stub / name
            p.write_text(body)
            p.chmod(0o755)

        device_file = Path(tmp, "jack-device")
        device_file.write_text("JACK_DEVICE=hw:9\nJACK_CARD_ID=TestDAC\nTIER=2\n")

        env = os.environ.copy()
        env.update({
            "PATH": f"{stub}:{env['PATH']}",
            "STUB_DIR": str(stub),
            "MPE_MODULE_REPO": str(REPO_ROOT),
            "MPE_RUN_DIR": str(run_dir),
            "MPE_JACK_DEVICE_FILE": str(device_file),
            "MPE_JACK_BUFFER": str(configured),
            "MPE_JACK_PERIODS": "2",
            "MPE_JACK_PROBE_TIMEOUT": "4",
        })
        log = Path(tmp, "out.log")
        state = run_dir / "jack.state"
        with log.open("w") as fh:
            proc = subprocess.Popen([str(START_JACKD)], env=env,
                                    stdout=fh, stderr=subprocess.STDOUT, text=True)
            try:
                # On success the script BLOCKS supervising jackd — that is the
                # pass condition, not an exit. Poll for the state it publishes.
                deadline = time.time() + timeout
                while time.time() < deadline:
                    if proc.poll() is not None:
                        break                      # exhausted ladder: it exits
                    if state.exists() and "period=" in state.read_text():
                        break
                    time.sleep(0.2)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
        return log.read_text(), (state.read_text() if state.exists() else "")

    def test_negative_control_a_workable_period_does_not_climb(self):
        """If this climbs, the ladder fires when it should not and the rest is noise."""
        out, state = self._run(configured=128, min_workable=128)
        self.assertIn("period=128", state)
        self.assertIn("requested_period=128", state)
        self.assertNotIn("Latency is higher than configured", out)

    def test_64_climbs_to_128_and_says_so(self):
        """The measured appliance failure, reproduced."""
        out, state = self._run(configured=64, min_workable=128)
        self.assertIn("period=128", state)
        self.assertIn("requested_period=64", state,
                      "the requested period must survive into state, or the "
                      "player cannot account for the extra latency")
        self.assertIn("Latency is higher than configured", out)
        self.assertIn("no driver at 64", out)

    def test_climbs_past_128_to_256(self):
        out, state = self._run(configured=64, min_workable=256)
        self.assertIn("period=256", state)
        self.assertIn("requested_period=64", state)

    def test_exhausted_ladder_fails_loudly_instead_of_running_silent(self):
        """A graph nobody can attach to must not be reported as a working one."""
        out, _ = self._run(configured=64, min_workable=99999)
        self.assertIn("no period in the ladder started a driver", out)


if __name__ == "__main__":
    unittest.main()
