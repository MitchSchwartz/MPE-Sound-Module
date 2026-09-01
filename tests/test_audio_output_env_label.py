"""/etc/mpe/mpe.env must survive being sourced, whatever the DAC calls itself.

Regression for 2026-09-01, reported by Mitch as "line 52 command not found".
Selecting the FiiO wrote:

    MPE_AUDIO_OUTPUT_LABEL=FiiO KA1

unquoted. mpe.env is SOURCED by bash, where that line means "set the var to
FiiO, then run the command KA1", so every script that sourced the file failed
at line 52. The label is the only free-form value the file holds and it is
VENDOR-CONTROLLED -- the product string is whatever the device reports.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib"

# The real product strings on the appliance, plus hostile shapes a vendor could
# legally put in a USB product descriptor.
LABELS = [
    "FiiO KA1",
    "Scarlett 4i4 USB",
    "USB-C to 3.5mm Headphone Jack A",
    "KM-HIFI-384KHZ",
    'evil"; rm -rf /tmp/pwned; #',
    "$(touch /tmp/pwned)",
    "`touch /tmp/pwned`",
    "a/b&c",
    "trailing\nnewline",
    "",
]


def _sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c",
         f'source "{LIB}/audio-engine.sh"; source "{LIB}/audio-outputs.sh"; {script}'],
        capture_output=True, text=True, timeout=30)


class EnvFileStaysSourceableTests(unittest.TestCase):
    def test_every_label_produces_a_sourceable_line(self):
        """The actual failure: the file stopped being sourceable."""
        for label in LABELS:
            with self.subTest(label=label):
                value = _sh(f'mpe_output_label_env_value {label!r}').stdout
                with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
                    fh.write("MPE_JACK_BUFFER=96\n")
                    fh.write(f"MPE_AUDIO_OUTPUT_LABEL={value}\n")
                    path = fh.name
                r = subprocess.run(["bash", "-c", f'source {path}'],
                                   capture_output=True, text=True, timeout=30)
                Path(path).unlink()
                self.assertEqual(r.returncode, 0, f"{label!r} -> {value!r}: {r.stderr}")
                self.assertEqual(r.stderr, "", f"{label!r} -> {value!r}")

    def test_a_spaced_label_round_trips_intact(self):
        """Quoting must not silently truncate the name at the first space --
        naming the absent device is the whole reason the label is stored."""
        value = _sh("mpe_output_label_env_value 'Scarlett 4i4 USB'").stdout
        r = subprocess.run(
            ["bash", "-c", f'MPE_AUDIO_OUTPUT_LABEL={value}; printf "%s" "$MPE_AUDIO_OUTPUT_LABEL"'],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout, "Scarlett 4i4 USB")

    def test_a_label_cannot_execute_a_command(self):
        """Negative control on the control: prove the hostile label WOULD have
        run something before sanitising, so this test can fail."""
        marker = Path(tempfile.gettempdir()) / "mpe-label-injection-canary"
        marker.unlink(missing_ok=True)
        value = _sh(f'mpe_output_label_env_value {"$(touch " + str(marker) + ")"!r}').stdout
        subprocess.run(["bash", "-c", f'MPE_AUDIO_OUTPUT_LABEL={value}'],
                       capture_output=True, text=True, timeout=30)
        self.assertFalse(marker.exists(), "a product string executed a command")

    def test_unsanitised_injection_really_does_fire(self):
        """Instrument validation. Without this the test above could pass because
        the injection never worked, not because sanitising stopped it."""
        marker = Path(tempfile.gettempdir()) / "mpe-label-canary-control"
        marker.unlink(missing_ok=True)
        subprocess.run(["bash", "-c", f'MPE_AUDIO_OUTPUT_LABEL=$(touch {marker})'],
                       capture_output=True, text=True, timeout=30)
        fired = marker.exists()
        marker.unlink(missing_ok=True)
        self.assertTrue(fired, "the injection shape does not fire — the guard proves nothing")


class SanitiseTests(unittest.TestCase):
    def test_sed_metacharacters_are_removed(self):
        """The value reaches a sed REPLACEMENT in _update_env_var and in
        mpe_pending_reconcile. `&` expands to the whole match; `/` ends the
        expression. Either corrupts the file the appliance boots from."""
        out = _sh("mpe_output_label_sanitize 'a/b&c'").stdout
        self.assertEqual(out, "abc")

    def test_ordinary_product_names_are_left_alone(self):
        for name in ("FiiO KA1", "Scarlett 4i4 USB", "KM-HIFI-384KHZ",
                     "USB-C to 3.5mm Headphone Jack A"):
            self.assertEqual(_sh(f"mpe_output_label_sanitize {name!r}").stdout, name)

    def test_newlines_cannot_inject_a_second_key(self):
        out = _sh("mpe_output_label_sanitize 'X\nMPE_JACK_BUFFER=32'").stdout
        self.assertNotIn("\n", out)


class LivesInTheLibraryTests(unittest.TestCase):
    """A function inside a top-to-bottom script cannot be sourced by a test, and
    "the test could only assert the script's TEXT" is exactly how the buffer
    validator shipped undefined behind 1905 passing tests."""

    def test_the_sanitiser_is_sourceable(self):
        self.assertEqual(_sh("type -t mpe_output_label_sanitize").stdout.strip(), "function")

    def test_the_script_does_not_keep_its_own_copy(self):
        src = (REPO / "scripts" / "set-surge-audio.sh").read_text(encoding="utf-8")
        self.assertNotIn("_sanitize_env_label", src)
        self.assertIn("mpe_output_label_env_value", src)


if __name__ == "__main__":
    unittest.main()
