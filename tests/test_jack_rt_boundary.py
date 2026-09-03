"""Phase 5 realtime boundary guards (session-control-plane-spec criteria 33, 36)."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"

# Python must not register JACK process callbacks. Compiled clients live in native/.
#
# REVIEWED EXCEPTION, 2026-09-02: scripts/measure-midi-audio-latency.py.
#
# It is a MEASUREMENT INSTRUMENT, never appliance runtime -- no unit starts it
# (asserted by test_measurement_harness_is_not_wired_into_any_unit below), it is
# run by hand for ~35 s and torn down, and it exists to measure the one leg JACK
# cannot report: Surge's MIDI-in to audio-out latency, which the looper-grid
# offset omitted entirely until it was measured.
#
# The rule is right and this client proves it: JACK's xrun callback counted 1-4
# graph overruns per window while it was attached. The journal recorded ZERO
# ALSA underruns, so nothing became inaudible, and the reading it produces is a
# FRAME DELTA rather than a rate -- so unlike an xrun count it is not distorted
# by the probe's own presence. That is the whole reason a Python client is
# tolerable here and nowhere else.
JACK_CALLBACK_ALLOWLIST: frozenset[str] = frozenset({
    "scripts/measure-midi-audio-latency.py",
})

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
    def test_measurement_harness_is_not_wired_into_any_unit(self) -> None:
        """An allowlisted Python JACK client must never become appliance runtime.

        The exemption above is justified ONLY because the harness is run by hand
        and torn down. If a unit ever started it, a Python process callback would
        be live on the graph during a gig -- exactly what criterion 33 forbids.
        This makes that impossible to do by accident.
        """
        units = sorted(CONFIG.glob("*.service"))
        # Units live in config/, not systemd/. The first version of this guard
        # scanned a directory that does not exist, found nothing, and passed --
        # a test that cannot fail is not a guard. Assert the corpus is non-empty
        # before trusting a clean result from it.
        self.assertGreater(len(units), 10, "found almost no unit files — the scan path is wrong")

        offenders = []
        for rel in sorted(JACK_CALLBACK_ALLOWLIST):
            name = Path(rel).name
            for unit in units:
                if name in unit.read_text(encoding="utf-8", errors="ignore"):
                    offenders.append(f"  {unit.relative_to(REPO).as_posix()} references {name}")
        if offenders:
            self.fail(
                "An allowlisted Python JACK client is referenced by a systemd unit.\n"
                "It would then run on the live graph, which criterion 33 forbids:\n"
                + "\n".join(offenders)
            )

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

    def test_peak_meter_unit_restarts_on_failure(self) -> None:
        """JACK drop exits 1 → restart; disabled exits 0 → stays inactive."""
        text = (CONFIG / "mpe-peak-meter.service").read_text(encoding="utf-8")
        self.assertEqual(_directive(text, "Restart"), ["on-failure"])
        self.assertEqual(_directive(text, "ExecStartPre"), [])

    def test_install_units_does_not_disable_the_peak_meter(self) -> None:
        """Listing it in DISABLED ran `systemctl disable` on every deploy.

        Observed on the appliance 2026-08-18: the unit was enabled by hand, then
        install-units.sh printed "disabled: mpe-peak-meter (intentional)". Since
        configure-pi-paths.sh runs install-units.sh, the OUT meter would switch itself
        off on the next deploy with MPE_PEAK_METER=1 still set and nothing logged.
        The start script already gates on the flag, so the unit is safe left enabled.
        """
        text = (REPO / "scripts" / "install-units.sh").read_text(encoding="utf-8")
        block = re.search(r"^DISABLED=\((.*?)^\)", text, re.M | re.S)
        self.assertIsNotNone(block, "DISABLED array not found in install-units.sh")
        entries = [
            line.strip()
            for line in block.group(1).splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertNotIn(
            "mpe-peak-meter",
            entries,
            "mpe-peak-meter must not be in DISABLED — it gates on MPE_PEAK_METER in "
            "start-mpe-peak-meter.sh, and listing it disables the meter on every deploy",
        )

    def test_sdl_audio_is_disabled_appliance_wide(self) -> None:
        """Audio-path entry point 2: nothing may open a PCM but the DAC.

        pygame.init() initialises every subsystem including the mixer, so the touch UI,
        the boot splash and the calibration loader each held the Pi's onboard headphone
        jack open, streaming 44.1 kHz silence on a second clock domain. Measured on the
        appliance 2026-08-18: 41 xruns / 75 s with DSP never above 55%. Nobody wrote a
        line of audio code — see Documents/DECISIONS.md 2026-08-18.
        """
        env_example = REPO / "config" / "mpe.env.example"
        self.assertTrue(env_example.is_file(), "config/mpe.env.example missing")
        self.assertIn(
            "SDL_AUDIODRIVER=dummy",
            env_example.read_text(encoding="utf-8"),
            "SDL_AUDIODRIVER=dummy must ship in mpe.env.example — pygame.init() otherwise "
            "opens the onboard PCM at every call site",
        )

    def test_no_bare_pygame_init_without_audio_guard(self) -> None:
        """Every pygame.init() call site inherits the mixer unless SDL is neutered.

        This test does not forbid pygame.init(); it records the call sites so a new one
        cannot be added silently while the mitigation is an env var rather than code.
        """
        sites = []
        for root in (REPO / "patch_browser", REPO / "scripts", REPO):
            for path in sorted(root.glob("*.py")) + sorted(root.rglob("*.py") if root != REPO else []):
                rel = path.relative_to(REPO).as_posix()
                if rel.startswith("tests/") or rel in [s[0] for s in sites]:
                    continue
                text = path.read_text(encoding="utf-8")
                if "pygame.init()" in text:
                    sites.append((rel, text.count("pygame.init()")))
        total = sum(n for _, n in sites)
        self.assertLessEqual(
            total,
            12,
            "new pygame.init() call site(s) added; each opens an audio device unless "
            f"SDL_AUDIODRIVER=dummy is set. Sites: {sites}",
        )


if __name__ == "__main__":
    unittest.main()
