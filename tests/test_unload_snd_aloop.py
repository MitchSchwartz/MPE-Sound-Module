"""start-surge-cli.sh's snd-aloop tidy-up.

It removes snd-aloop whenever the refcount is 0, on the assumption that a
loaded snd-aloop can only be leftover calibration plumbing. That assumption
still holds: the idle sink is snd-dummy on a different card index, so this
cannot reach it. The tests pin the tidy-up in place and cover the sourced-exit
bug found while checking it -- the early-out used `exit`, which in a sourced
script would have taken start-surge-cli.sh down with it.
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
    def _run(self, modules):
        """Run the script with a fake `sudo` on PATH; return what it invoked."""
        tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        calls = tmp / "calls.log"
        (bin_dir / "sudo").write_text(f'#!/bin/bash\necho "$*" >> {calls}\n')
        (bin_dir / "sudo").chmod(0o755)

        modules_file = tmp / "modules"
        modules_file.write_text(modules)
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "MPE_MODULES_FILE": str(modules_file),
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return calls.read_text() if calls.exists() else ""

    def test_leftover_calibration_aloop_is_unloaded(self):
        """A refcount-0 snd-aloop is calibration residue and gets removed."""
        self.assertIn("modprobe -r snd_aloop", self._run(MODULES_IDLE))

    def test_a_referenced_aloop_is_left_alone(self):
        """Refcount > 0 means something is using it — leave it alone."""
        self.assertEqual(self._run(MODULES_BOUND), "")

    def test_an_unreadable_modules_file_returns_rather_than_exits(self):
        """The sourced-exit bug: `exit` here killed start-surge-cli.sh."""
        script = SCRIPT.read_text()
        self.assertNotIn("    exit 0\nfi", script)
        self.assertIn("return 0 2>/dev/null || exit 0", script)


if __name__ == "__main__":
    unittest.main()
