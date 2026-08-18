"""Phase 5 realtime boundary guards (session-control-plane-spec criteria 33, 36)."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"

# Python must not register JACK process callbacks. Compiled clients live in native/.
JACK_CALLBACK_ALLOWLIST: frozenset[str] = frozenset()

# Units that host a JACK client must declare RT limits (criterion 36).
JACK_CLIENT_UNITS: dict[str, str] = {
    "mpe-jackd": "jackd realtime server",
    "surge-xt-cli": "Surge XT JACK client",
    "mpe-sooperlooper": "SooperLooper JACK client",
    "mpe-peak-meter": "compiled OUT peak meter",
}


def _scan_python_jack_callbacks() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    pattern = re.compile(r"\.set_process_callback\s*\(")
    roots = (REPO / "patch_browser", REPO / "scripts")
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel in JACK_CALLBACK_ALLOWLIST:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if pattern.search(line):
                    hits.append((rel, lineno, line.strip()))
    return hits


def _directive(text: str, key: str) -> list[str]:
    return re.findall(rf"^{re.escape(key)}=(.*)$", text, re.M)


class JackRtBoundaryTests(unittest.TestCase):
    def test_no_python_jack_process_callbacks(self) -> None:
        """Criterion 33: set_process_callback outside the allowlist fails the suite."""
        hits = _scan_python_jack_callbacks()
        if hits:
            detail = "\n".join(f"  {path}:{lineno}: {line}" for path, lineno, line in hits)
            self.fail(
                "Python JACK process callbacks are forbidden (Phase 5 / D1).\n"
                f"Move the client to native/ or add a reviewed allowlist entry:\n{detail}"
            )

    def test_scanner_detects_set_process_callback(self) -> None:
        """Criterion 33: the guard must catch a new Python JACK callback."""
        probe = REPO / "patch_browser" / "_rt_boundary_probe.py"
        probe.write_text("client.set_process_callback(cb)\n", encoding="utf-8")
        try:
            hits = _scan_python_jack_callbacks()
            self.assertIn(
                ("patch_browser/_rt_boundary_probe.py", 1, "client.set_process_callback(cb)"),
                hits,
            )
        finally:
            probe.unlink(missing_ok=True)

    def test_mpe_peak_meter_unit_in_disabled_list(self) -> None:
        """Opt-in meter: installed but not enabled until operator sets MPE_PEAK_METER=1."""
        text = (REPO / "scripts" / "install-units.sh").read_text(encoding="utf-8")
        block = text.split("DISABLED=(", 1)[1].split(")", 1)[0]
        self.assertIn("mpe-peak-meter", block)

    def test_jack_client_units_declare_limit_rtprio(self) -> None:
        """Criterion 36: every unit hosting a JACK client declares LimitRTPRIO."""
        for name, reason in JACK_CLIENT_UNITS.items():
            unit_path = CONFIG / f"{name}.service"
            self.assertTrue(unit_path.is_file(), f"missing unit for {reason}: {name}")
            text = unit_path.read_text(encoding="utf-8")
            self.assertEqual(
                _directive(text, "LimitRTPRIO"),
                ["95"],
                f"{name} must set LimitRTPRIO=95 ({reason})",
            )
            self.assertEqual(
                _directive(text, "LimitMEMLOCK"),
                ["infinity"],
                f"{name} must set LimitMEMLOCK=infinity ({reason})",
            )

    def test_touch_browser_is_not_a_jack_client_host(self) -> None:
        """Edge plane: touch UI must not carry RT limits for a JACK callback."""
        text = (CONFIG / "touch-patch-browser.service").read_text(encoding="utf-8")
        self.assertEqual(
            _directive(text, "LimitRTPRIO"),
            [],
            "touch-patch-browser must not declare LimitRTPRIO after Phase 5",
        )


    def test_mpe_peak_meter_compiles_when_libjack_present(self) -> None:
        """Criterion 34: the compiled client must build where libjack-dev is installed."""
        if subprocess.run(["pkg-config", "--exists", "jack"], capture_output=True).returncode != 0:
            self.skipTest("libjack-dev not installed on this host")
        native = REPO / "native" / "mpe-peak-meter"
        proc = subprocess.run(["make", "-C", str(native), "check"], capture_output=True, text=True)
        self.assertEqual(
            proc.returncode,
            0,
            f"mpe-peak-meter failed to compile:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertTrue((native / "mpe-peak-meter").is_file())

    def test_peak_meter_unit_has_no_condition_environment(self) -> None:
        """ConditionEnvironment reads the manager env, not EnvironmentFile — ghost skip."""
        text = (CONFIG / "mpe-peak-meter.service").read_text(encoding="utf-8")
        self.assertEqual(
            _directive(text, "ConditionEnvironment"),
            [],
            "mpe-peak-meter must gate on MPE_PEAK_METER in start script, not ConditionEnvironment",
        )

    def test_peak_meter_unit_restarts_always(self) -> None:
        """JACK graph restarts must bring the meter back (non-zero exit on shutdown)."""
        text = (CONFIG / "mpe-peak-meter.service").read_text(encoding="utf-8")
        self.assertEqual(_directive(text, "Restart"), ["always"])
        self.assertEqual(_directive(text, "ExecStartPre"), [])

if __name__ == "__main__":
    unittest.main()
