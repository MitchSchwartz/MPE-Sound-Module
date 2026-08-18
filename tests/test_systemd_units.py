"""Guards on the shipped systemd units.

Written after two related failures:

* ``mpe-looper.service`` was enabled for five days while doing nothing, because its
  ``ConditionPathExists`` was never met — ``systemctl is-enabled`` said enabled,
  nothing reported failed, and every caller reported success for work that never
  happened (retired in a310449).
* The looper stack (engine, APC bench, HUD writer) had no units at all and ran as
  hand-started ``setsid nohup`` processes. On 2026-08-17 the engine died at 16:15
  and nothing restarted it for six hours.

Both are the same class of bug: something the appliance depends on, with nothing
watching it. These tests assert the units exist, name real files, and are supervised.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"
INSTALL_UNITS = REPO / "scripts" / "install-units.sh"

LOOPER_UNITS = ("mpe-sooperlooper", "mpe-looper-session")


def _enabled_units() -> list[str]:
    """Unit names from the ENABLED=( ... ) array in install-units.sh."""
    text = INSTALL_UNITS.read_text(encoding="utf-8")
    block = text.split("ENABLED=(", 1)[1].split(")", 1)[0]
    names = []
    for raw in block.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def _unit_text(name: str) -> str:
    return (CONFIG / f"{name}.service").read_text(encoding="utf-8")


def _directive(text: str, key: str) -> list[str]:
    return re.findall(rf"^{re.escape(key)}=(.*)$", text, re.M)


class EnabledUnitsExistTests(unittest.TestCase):
    def test_every_enabled_unit_has_a_file(self) -> None:
        for name in _enabled_units():
            self.assertTrue(
                (CONFIG / f"{name}.service").is_file(),
                f"{name} is in ENABLED but config/{name}.service does not exist",
            )

    def test_every_exec_path_in_the_repo_exists(self) -> None:
        """The ghost-unit failure: enabled, silent, and pointing at nothing."""
        for name in _enabled_units():
            text = _unit_text(name)
            for key in ("ExecStart", "ExecStartPre", "ExecStartPost"):
                for line in _directive(text, key):
                    for token in line.split():
                        token = token.lstrip("-@:+!")
                        if not token.startswith("@MPE_MODULE_REPO@"):
                            continue
                        rel = token.replace("@MPE_MODULE_REPO@/", "", 1)
                        self.assertTrue(
                            (REPO / rel).exists(),
                            f"{name}: {key} points at missing repo file {rel}",
                        )

    def test_repo_exec_scripts_are_executable(self) -> None:
        import os

        for name in _enabled_units():
            text = _unit_text(name)
            for key in ("ExecStart", "ExecStartPre", "ExecStartPost"):
                for line in _directive(text, key):
                    for token in line.split():
                        token = token.lstrip("-@:+!")
                        if not token.startswith("@MPE_MODULE_REPO@"):
                            continue
                        rel = token.replace("@MPE_MODULE_REPO@/", "", 1)
                        path = REPO / rel
                        if path.suffix == ".py":
                            continue  # invoked via the interpreter
                        self.assertTrue(
                            os.access(path, os.X_OK),
                            f"{name}: {rel} is not executable",
                        )


class LooperStackIsSupervisedTests(unittest.TestCase):
    """The looper must not go back to hand-started processes."""

    def test_looper_units_are_enabled(self) -> None:
        enabled = _enabled_units()
        for name in LOOPER_UNITS:
            self.assertIn(name, enabled, f"{name} is not in install-units.sh ENABLED")

    def test_looper_units_restart_always(self) -> None:
        for name in LOOPER_UNITS:
            self.assertEqual(
                _directive(_unit_text(name), "Restart"),
                ["always"],
                f"{name} must Restart=always — an unsupervised looper is the 2026-08-17 bug",
            )

    def test_looper_units_have_an_install_section(self) -> None:
        """No [Install] means `systemctl enable` silently does nothing."""
        for name in LOOPER_UNITS:
            self.assertIn("[Install]", _unit_text(name), f"{name} cannot be enabled")

    def test_no_condition_path_exists_on_looper_units(self) -> None:
        """The ghost unit skipped every boot on an unmet ConditionPathExists."""
        for name in LOOPER_UNITS:
            self.assertEqual(
                _directive(_unit_text(name), "ConditionPathExists"),
                [],
                f"{name}: a ConditionPathExists here can skip silently — see a310449",
            )

    def test_engine_is_not_bound_to_jackd(self) -> None:
        """A jackd restart must orphan the engine, not stop it — sl-watchdog repairs that."""
        text = _unit_text("mpe-sooperlooper")
        self.assertEqual(_directive(text, "BindsTo"), [])
        self.assertEqual(_directive(text, "Requires"), [])
        self.assertTrue(
            any("mpe-jackd.service" in line for line in _directive(text, "After")),
            "engine should still be ordered after jackd",
        )

    def test_clients_are_not_bound_to_the_engine(self) -> None:
        """Bench and HUD recover on their own; binding kills them on every restart."""
        for name in ("mpe-looper-session",):
            text = _unit_text(name)
            self.assertEqual(_directive(text, "BindsTo"), [], f"{name} must not BindsTo")
            self.assertEqual(_directive(text, "Requires"), [], f"{name} must not Requires")

    def test_engine_gets_realtime_limits(self) -> None:
        """systemd bypasses PAM, so limits.d does not apply to the JACK client."""
        text = _unit_text("mpe-sooperlooper")
        self.assertEqual(_directive(text, "LimitRTPRIO"), ["95"])
        self.assertEqual(_directive(text, "LimitMEMLOCK"), ["infinity"])


class SingleUnitSourceTests(unittest.TestCase):
    """`config/` is the only source of units. A second copy is a drift trap.

    There used to be a pre-rendered `systemd/` directory that install-units.sh read
    while configure-pi-paths.sh read `config/` — two committed copies of every unit.
    Adding to one silently missed the other, which is exactly what happened when the
    looper units landed (install-units.sh: "No such file or directory"). Collapsed
    2026-08-17; install-units.sh renders the templates itself.
    """

    SUBSTITUTIONS = {
        "@MPE_PI_USER@": "mitch",
        "@MPE_MODULE_REPO@": "/home/mitch/MPE-Module",
        "@MPE_SCRIPTS_DIR@": "/home/mitch/MPE-Module/scripts",
    }

    def test_there_is_no_second_unit_directory(self) -> None:
        self.assertFalse(
            (REPO / "systemd").exists(),
            "systemd/ is back — one source of truth for units, or they drift",
        )

    def test_both_installers_read_config(self) -> None:
        install = INSTALL_UNITS.read_text(encoding="utf-8")
        self.assertIn('SRC="$ROOT/config"', install)
        configure = (REPO / "scripts" / "configure-pi-paths.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"$MPE_MODULE_REPO/config/"*.service', configure)

    def test_installer_substitutes_every_placeholder_the_units_use(self) -> None:
        """A placeholder no installer substitutes ships a unit pointing at a literal."""
        install = INSTALL_UNITS.read_text(encoding="utf-8")
        used: set[str] = set()
        for path in CONFIG.glob("*.service"):
            used.update(re.findall(r"@MPE_[A-Z_]+@", path.read_text(encoding="utf-8")))
        for placeholder in sorted(used):
            self.assertIn(
                placeholder,
                install,
                f"{placeholder} appears in a unit but install-units.sh never renders it",
            )

    def test_installer_refuses_to_ship_unrendered_placeholders(self) -> None:
        install = INSTALL_UNITS.read_text(encoding="utf-8")
        self.assertIn("still has unsubstituted placeholders", install)

    def test_templates_render_without_leftovers(self) -> None:
        for path in sorted(CONFIG.glob("*.service")):
            text = path.read_text(encoding="utf-8")
            for placeholder, value in self.SUBSTITUTIONS.items():
                text = text.replace(placeholder, value)
            leftover = re.findall(r"@MPE_[A-Z_]+@", text)
            self.assertEqual(
                leftover, [], f"config/{path.name} uses unknown placeholder {leftover}"
            )


    def test_retired_looper_client_units_disabled(self) -> None:
        """Phase 3M: merged units must land in DISABLED so upgrade does not double-run."""
        install = INSTALL_UNITS.read_text(encoding="utf-8")
        disabled_block = install.split("DISABLED=(", 1)[1].split(")", 1)[0]
        for name in ("mpe-apc-bench", "sl-hud-monitor"):
            self.assertIn(name, disabled_block, f"{name} must be in install-units DISABLED")

    def test_retired_mpe_bench_is_gone_everywhere(self) -> None:
        """It could no longer free the APC — mpe-looper-session.service holds it now."""
        self.assertFalse((CONFIG / "mpe-bench.service").exists())
        provision = (
            REPO / "scripts" / "pi" / "provision-mpe-agent.sh"
        ).read_text(encoding="utf-8")
        units_line = next(
            line for line in provision.splitlines() if line.startswith("UNITS=")
        )
        self.assertNotIn("mpe-bench ", units_line)
        self.assertIn("mpe-looper-session", units_line)


class EngineLauncherTests(unittest.TestCase):
    def test_exec_start_does_not_background(self) -> None:
        """A wrapper that backgrounds its work makes Restart= watch the wrapper."""
        text = (REPO / "scripts" / "sooperlooper" / "run-sooperlooper.sh").read_text(
            encoding="utf-8"
        )
        body = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
        for forbidden in ("setsid", "nohup", "disown"):
            self.assertNotIn(
                forbidden, body, f"run-sooperlooper.sh must not {forbidden} under systemd"
            )
        self.assertRegex(
            body, re.compile(r'^exec "\$SOOP_BIN"', re.M), msg="engine must be exec'd"
        )

    def test_launcher_waits_for_jack(self) -> None:
        text = (REPO / "scripts" / "sooperlooper" / "run-sooperlooper.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("mpe_wait_for_jack_server", text)


if __name__ == "__main__":
    unittest.main()


class BenchXrunsIntegrityTests(unittest.TestCase):
    """The bench must not report a number the server was told not to produce.

    Shipped broken on 2026-08-17: --strict did `export MPE_JACK_SOFTMODE=0`, but jackd
    reads EnvironmentFile=/etc/mpe/mpe.env, so the flag never reached the server.
    Softmode suppresses jackd's xrun message, so every run counted a signal that had
    been switched off — "0 xruns" read identically whether the graph was clean or on
    fire. That is the exact failure the tool exists to prevent.
    """

    BENCH = REPO / "scripts" / "bench-xruns.sh"

    def _code(self) -> str:
        """Comments explain the old broken form by name — check the code only."""
        return "\n".join(
            line
            for line in self.BENCH.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        )

    def test_strict_writes_the_env_file_not_just_a_shell_var(self) -> None:
        code = self._code()
        self.assertNotIn(
            "export MPE_JACK_SOFTMODE",
            code,
            "exporting does not reach jackd — it reads /etc/mpe/mpe.env",
        )
        self.assertIn("_set_env_var MPE_JACK_SOFTMODE 0", code)

    def test_strict_restores_softmode_on_exit(self) -> None:
        text = self.BENCH.read_text(encoding="utf-8")
        self.assertIn("trap _restore_softmode EXIT", text)
        self.assertIn("_set_env_var MPE_JACK_SOFTMODE 1", text)

    def test_zero_in_softmode_is_reported_as_unknown_not_pass(self) -> None:
        text = self.BENCH.read_text(encoding="utf-8")
        self.assertIn("_assert_xrun_reporting_live", text)
        self.assertIn("UNKNOWN", text)
        self.assertIn("reporting is suppressed", text)
