"""The idle sink must survive Surge start.

scripts/lib/unload-snd-aloop.sh predates the idle sink: it removes snd-aloop
whenever the refcount is 0, on the assumption that a loaded snd-aloop is
leftover calibration plumbing. On a Pi 5 in usb-host with no external DAC that
assumption is wrong -- snd-aloop is the only free-running playback device the
graph has, and the refcount is legitimately 0 in the window between installing
it and jackd binding it.

MEASURED 2026-08-30: after a deploy that reported "idle sink loaded (card
Loopback)", `lsmod | grep aloop` on the Pi was empty and jackd was crashlooping
with "no ALSA card matches tier '4'".
"""

import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "lib" / "unload-snd-aloop.sh"

# refcount 0 -- the state the old code unloads on.
MODULES_IDLE = "snd_aloop 49152 0 - Live 0x0\nsnd_pcm 180224 7 - Live 0x0\n"
MODULES_BOUND = "snd_aloop 49152 1 - Live 0x0\nsnd_pcm 180224 7 - Live 0x0\n"


class UnloadAloopTests(unittest.TestCase):
    def _run(self, modules, *, idle_sink_conf):
        """Run the script with a fake `sudo` on PATH; return what it invoked."""
        with self.subTest(idle_sink=bool(idle_sink_conf)):
            pass
        tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        calls = tmp / "calls.log"
        (bin_dir / "sudo").write_text(f'#!/bin/bash\necho "$*" >> {calls}\n')
        (bin_dir / "sudo").chmod(0o755)

        modules_file = tmp / "modules"
        modules_file.write_text(modules)
        conf = tmp / "mpe-idle-sink.conf"
        if idle_sink_conf:
            conf.write_text("options snd-aloop index=7\n")

        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "MPE_MODULES_FILE": str(modules_file),
            "MPE_IDLE_SINK_CONF": str(conf),
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return calls.read_text() if calls.exists() else ""

    def test_the_idle_sink_is_not_unloaded_even_at_refcount_zero(self):
        """The regression. Refcount 0 is normal before jackd binds the card."""
        self.assertEqual(
            self._run(MODULES_IDLE, idle_sink_conf=True),
            "",
            "Surge start removed the appliance's only free-running clock",
        )

    def test_leftover_calibration_aloop_is_still_unloaded(self):
        """Positive control: without the idle-sink marker the old behaviour stands.

        Without this, a script that unloaded nothing ever would pass the test
        above -- which is the same reading whether the fix works or not.
        """
        self.assertIn(
            "modprobe -r snd_aloop",
            self._run(MODULES_IDLE, idle_sink_conf=False),
        )

    def test_a_referenced_aloop_is_left_alone(self):
        """Unchanged behaviour: refcount > 0 was never touched."""
        self.assertEqual(self._run(MODULES_BOUND, idle_sink_conf=False), "")


if __name__ == "__main__":
    unittest.main()
