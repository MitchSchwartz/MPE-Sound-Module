"""T3b — health sources must exist and be fresh at boot."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from patch_browser.health_source_liveness import (
    SourceSpec,
    check_source,
    specs_for_role,
    verify_or_exit,
    verify_role,
)


class HealthSourceLivenessTests(unittest.TestCase):
    def test_missing_meter_fails(self) -> None:
        spec = SourceSpec("meter.state", Path("/nonexistent/meter.state"))
        err = check_source(spec)
        self.assertIsNotNone(err)
        self.assertIn("missing", err)

    def test_stale_meter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meter.state"
            path.write_text(f"xruns=1\nupdated={int(time.time()) - 120}\n")
            spec = SourceSpec(
                "meter.state", path, max_age_s=3.0, required_keys=("xruns", "updated")
            )
            err = check_source(spec)
            self.assertIsNotNone(err)
            self.assertIn("stale", err)

    def test_fresh_meter_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meter.state"
            path.write_text(f"xruns=1\nupdated={int(time.time())}\n")
            spec = SourceSpec(
                "meter.state", path, max_age_s=3.0, required_keys=("xruns", "updated")
            )
            self.assertIsNone(check_source(spec))

    def test_verify_or_exit_raises_on_bad_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "meter.state"
            import os

            os.environ["MPE_METER_STATE"] = str(bad)
            with self.assertRaises(SystemExit) as ctx:
                verify_or_exit("sl-watchdog")
            self.assertEqual(1, ctx.exception.code)

    def test_sl_watchdog_role_spec(self) -> None:
        specs = specs_for_role("sl-watchdog")
        self.assertEqual(1, len(specs))
        self.assertEqual("meter.state", specs[0].name)


if __name__ == "__main__":
    unittest.main()
