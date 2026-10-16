"""Guard: tracked text files must not carry internal process references.

This repo is public and describes the product only. The terms below must not
appear in any tracked text file. Binary files are skipped (git grep -I).

Allow-list, genuine false positives only: the English word "claimant" (the
things that claim a MIDI note or a control) in the files listed below.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Built from fragments so this file does not match itself.
TERMS = [
    "sr" + r"&ed",
    "sr" + "ed",
    "sr" + "-ed",
    r"scientific research.{0,20}experimental",
    "tax" + " credit",
    r"cr" + r"a\b",
    "claim" + "ant",
    r"internal/" + "projects",
]
PATTERN = "|".join(TERMS)

# term -> files where it is a real-English false positive
CLAIMANT_FILES = {
    "Documents/reviews/CHARTER-looper-ownership-2026-08-30.md",
    "scripts/sooperlooper/binding_table.py",
    "scripts/sooperlooper/control_registry.py",
    "scripts/sooperlooper/led_compositor.py",
    "scripts/sooperlooper/loop_mix.py",
    "tests/test_control_registry.py",
}
ALLOWED_RE = re.compile("claim" + "ant", re.I)


class NoInternalProcessRefs(unittest.TestCase):
    def test_terms_absent_from_tracked_text(self):
        out = subprocess.run(
            ["git", "grep", "-I", "-n", "-i", "-E", PATTERN],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.splitlines()
        hits = []
        for line in out:
            path, _, rest = line.split(":", 2)[0], None, line.split(":", 2)[2]
            if path == "tests/test_no_internal_process_refs.py":
                continue
            if path in CLAIMANT_FILES:
                # drop the allowed word; anything else on the line still counts
                rest = ALLOWED_RE.sub("", rest)
                if not re.search(PATTERN, rest, re.I):
                    continue
            hits.append(line[:160])
        self.assertEqual(hits, [], "internal process references found:\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
