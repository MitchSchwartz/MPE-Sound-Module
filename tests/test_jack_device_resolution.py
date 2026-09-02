"""resolve_jack_device_index must pick the JACK driver, not a device NAMED "Jack".

THE BUG (2026-09-01, measured on the appliance). The resolver ran:

    grep "Output Audio Device" | grep -i "JACK" | sed ... | head -1

The attached DAC is named "USB-C to 3.5mm Headphone Jack A". `grep -i jack`
matched ten ALSA entries for that device before the single real JACK entry, and
head -1 took the first. Surge opened the raw ALSA device instead of the graph,
the device does not support 48000 Hz, and Surge exited -- while start-surge-cli.sh
logged "JACK client" and engine.state read engine=jack active=jack state=ok.

The Scarlett 4i4 has no "Jack" in its name, which is the only reason the same
code worked minutes earlier. That is the bug's signature: it is a property of
the DAC's product name, not of the code path taken.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
START_SURGE = REPO_ROOT / "scripts" / "start-surge-cli.sh"

# Captured verbatim from the appliance, 2026-09-01.
REAL_LISTING = """\
14:51:20.714 - Output Audio Device: [0.8] : ALSA.USB-C to 3.5mm Headphone Jack A, USB Audio; Direct hardware device without any conversions
14:51:20.714 - Output Audio Device: [0.9] : ALSA.USB-C to 3.5mm Headphone Jack A, USB Audio; Front output / input
14:51:20.714 - Output Audio Device: [0.17] : ALSA.USB-C to 3.5mm Headphone Jack A, USB Audio; Direct sample mixing device
14:51:20.731 - Output Audio Device: [1.0] : JACK.system
"""

SCARLETT_LISTING = """\
14:20:01.001 - Output Audio Device: [0.4] : ALSA.Scarlett 4i4 USB, USB Audio; Direct hardware device without any conversions
14:20:01.002 - Output Audio Device: [1.0] : JACK.system
"""

NO_JACK_LISTING = """\
14:20:01.001 - Output Audio Device: [0.4] : ALSA.USB-C to 3.5mm Headphone Jack A, USB Audio; Direct hardware device
"""


class ResolveJackDeviceIndexTests(unittest.TestCase):
    """Exercises the shell function itself — not a Python re-implementation."""

    @staticmethod
    def _resolve(listing: str) -> tuple[str, int]:
        src = START_SURGE.read_text(encoding="utf-8")
        body = src[src.index("resolve_jack_device_index() {"):]
        body = body[:body.index("\n}\n") + 3]
        # Replace the device-listing call with the fixture; the parsing under
        # test is everything after it.
        body = body.replace(
            'list="$(timeout 5 "$SURGE_CLI" --list-devices 2>&1)" || true',
            'list="$FIXTURE"')
        script = f"{body}\nresolve_jack_device_index\n"
        proc = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=30, env={"FIXTURE": listing,
                                                          "PATH": "/usr/bin:/bin"})
        return proc.stdout.strip(), proc.returncode

    def test_a_dac_named_jack_does_not_win(self):
        """The regression. [0.8] is ALSA; [1.0] is the graph."""
        index, rc = self._resolve(REAL_LISTING)
        self.assertEqual(rc, 0)
        self.assertEqual(index, "1.0",
                         "picked the ALSA device whose product name contains 'Jack'")

    def test_negative_control_the_old_grep_really_did_pick_the_dac(self):
        """If a bare `grep -i jack` no longer mispicks, this suite guards nothing."""
        proc = subprocess.run(
            ["bash", "-c",
             'printf "%s" "$FIXTURE" | grep "Output Audio Device" | grep -i "JACK" '
             r"""| sed -n 's/.*\[\([0-9][0-9]*\.[0-9][0-9]*\)\].*/\1/p' | head -1"""],
            capture_output=True, text=True, timeout=30,
            env={"FIXTURE": REAL_LISTING, "PATH": "/usr/bin:/bin"})
        self.assertEqual(proc.stdout.strip(), "0.8",
                         "the historical bug did not reproduce")

    def test_ordinary_dac_still_resolves(self):
        index, rc = self._resolve(SCARLETT_LISTING)
        self.assertEqual(rc, 0)
        self.assertEqual(index, "1.0")

    def test_no_jack_device_fails_rather_than_guessing(self):
        """With no graph, returning an ALSA index would silently bypass JACK."""
        index, rc = self._resolve(NO_JACK_LISTING)
        self.assertNotEqual(rc, 0)
        self.assertEqual(index, "")

    def test_resolver_does_not_use_an_unanchored_jack_grep(self):
        src = START_SURGE.read_text(encoding="utf-8")
        body = src[src.index("resolve_jack_device_index() {"):]
        body = body[:body.index("\n}\n")]
        code = [l for l in body.splitlines() if not l.strip().startswith("#")]
        self.assertNotIn('grep -i "JACK"', "\n".join(code),
                         "a bare case-insensitive jack grep matches product names")


if __name__ == "__main__":
    unittest.main()
