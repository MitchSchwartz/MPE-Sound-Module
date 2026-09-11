"""A patch the pinned engine cannot fully read must not reach the Pi.

WHY THIS EXISTS. Surge stamps `<patch revision="N">` into every .fxp, and the
number migrates forward only. A newer engine upgrades an older patch on load.
An older engine handed a NEWER patch does not refuse it — it loads what it
recognises and silently defaults the rest. The patch shows up in the browser,
plays, and sounds wrong. No error, no log line, no failed deploy.

The appliance's engine is pinned at 253f8d86 (a pre-1.4 nightly), so its
ceiling is fixed. Patches are authored on the PC, where Surge is whatever the
package manager last installed — that is the side free to drift upward. As
measured 2026-09-08 both write revision 24, so nothing is blocked today; this
guards the day a 1.4.x lands on the PC and raises it without saying so.

That is the project's recurring shape again: a result that looks identical
whether the thing worked or not. Prose in a README cannot fail a deploy; an
exit code can.

WHAT THIS DOES. Drives the real scripts/lib/patch-format-revision.sh against
fixture .fxp files and checks the return codes.

WHAT THIS DOES NOT DO. It does not test Surge, the Pi, or whether revision 24
is still the right ceiling — that number tracks the pin and must be edited when
the pin moves.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REV_LIB = REPO_ROOT / "scripts" / "lib" / "patch-format-revision.sh"

# The bytes that matter: an FXP container with Surge's XML chunk inside it.
FXP_HEAD = b"CcnK\x00\x00\x00\x00FPCh"


def _fxp(revision: int) -> bytes:
    return (
        FXP_HEAD
        + b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        + f'<patch revision="{revision}"><meta name="T" category="C"/></patch>'.encode()
    )


def _bash(body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", f"source {REV_LIB!s}; {body}"],
        capture_output=True,
        text=True,
        env=run_env,
        check=False,
    )


class PatchFormatRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, payload: bytes) -> None:
        target = self.dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def test_reads_revision_from_fxp(self) -> None:
        self._write("a.fxp", _fxp(24))
        result = _bash(f'mpe_patch_revision "{self.dir}/a.fxp"')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "24")

    def test_patch_at_the_ceiling_passes(self) -> None:
        self._write("Quick Select/at-max.fxp", _fxp(24))
        result = _bash(f'mpe_patch_revision_check "{self.dir}"')
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_older_patch_passes(self) -> None:
        # The stock library spans revisions 4-20; the pinned engine migrates
        # those up on load. Old is always safe.
        for rev in (4, 9, 20):
            self._write(f"old-{rev}.fxp", _fxp(rev))
        result = _bash(f'mpe_patch_revision_check "{self.dir}"')
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_newer_patch_is_refused_and_named(self) -> None:
        self._write("Quick Select/from-a-newer-surge.fxp", _fxp(25))
        result = _bash(f'mpe_patch_revision_check "{self.dir}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("from-a-newer-surge.fxp", result.stderr)
        self.assertIn("25", result.stderr)

    def test_one_bad_patch_fails_the_whole_tree(self) -> None:
        self._write("good.fxp", _fxp(24))
        self._write("bad.fxp", _fxp(99))
        result = _bash(f'mpe_patch_revision_check "{self.dir}"')
        self.assertNotEqual(result.returncode, 0)

    def test_stub_file_fails_closed(self) -> None:
        # Two 3-byte files reading b"fxp" were found in the PC's own patch
        # folder on 2026-09-08. Unreadable is not "revision 0" -- shipping one
        # produces the dead browser entry this guard exists to prevent.
        self._write("stub.fxp", b"fxp")
        result = _bash(f'mpe_patch_revision_check "{self.dir}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unreadable", result.stderr)

    def test_ceiling_is_overridable(self) -> None:
        self._write("rev25.fxp", _fxp(25))
        blocked = _bash(f'mpe_patch_revision_check "{self.dir}"')
        self.assertNotEqual(blocked.returncode, 0)
        allowed = _bash(
            f'mpe_patch_revision_check "{self.dir}"',
            {"MPE_PATCH_REVISION_MAX": "25"},
        )
        self.assertEqual(allowed.returncode, 0, msg=allowed.stderr)

    def test_empty_tree_passes(self) -> None:
        result = _bash(f'mpe_patch_revision_check "{self.dir}"')
        self.assertEqual(result.returncode, 0, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
