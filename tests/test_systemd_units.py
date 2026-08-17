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

LOOPER_UNITS = ("mpe-sooperlooper", "mpe-apc-bench", "sl-hud-monitor")


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
        for name in ("mpe-apc-bench", "sl-hud-monitor"):
            text = _unit_text(name)
            self.assertEqual(_directive(text, "BindsTo"), [], f"{name} must not BindsTo")
            self.assertEqual(_directive(text, "Requires"), [], f"{name} must not Requires")

    def test_engine_gets_realtime_limits(self) -> None:
        """systemd bypasses PAM, so limits.d does not apply to the JACK client."""
        text = _unit_text("mpe-sooperlooper")
        self.assertEqual(_directive(text, "LimitRTPRIO"), ["95"])
        self.assertEqual(_directive(text, "LimitMEMLOCK"), ["infinity"])


class RenderedUnitsMatchTemplatesTests(unittest.TestCase):
    """`config/` holds templates; `systemd/` holds the rendered copies.

    Two committed sources for one unit is a drift trap, and it bit immediately: the
    looper units were added to config/ only, so install-units.sh — which reads
    systemd/ — failed with "No such file or directory" on the appliance. Until the
    duplication is collapsed, assert the two stay in sync.
    """

    SUBSTITUTIONS = {
        "@MPE_PI_USER@": "mitch",
        "@MPE_MODULE_REPO@": "/home/mitch/MPE-Module",
        "@MPE_SCRIPTS_DIR@": "/home/mitch/MPE-Module/scripts",
    }
    SYSTEMD = REPO / "systemd"

    def _render(self, text: str) -> str:
        for placeholder, value in self.SUBSTITUTIONS.items():
            text = text.replace(placeholder, value)
        return text

    def test_every_enabled_unit_has_a_rendered_copy(self) -> None:
        for name in _enabled_units():
            self.assertTrue(
                (self.SYSTEMD / f"{name}.service").is_file(),
                f"{name} is in ENABLED but systemd/{name}.service is missing — "
                f"install-units.sh reads systemd/, not config/",
            )

    def test_rendered_copies_match_their_templates(self) -> None:
        for path in sorted(self.SYSTEMD.glob("*.service")):
            template = CONFIG / path.name
            if not template.is_file():
                continue  # systemd-only units (e.g. mpe-bench) have no template
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                self._render(template.read_text(encoding="utf-8")),
                f"systemd/{path.name} has drifted from config/{path.name} — "
                f"re-render it after editing the template",
            )

    def test_rendered_copies_have_no_unsubstituted_placeholders(self) -> None:
        for path in sorted(self.SYSTEMD.glob("*.service")):
            leftover = re.findall(r"@MPE_[A-Z_]+@", path.read_text(encoding="utf-8"))
            self.assertEqual(leftover, [], f"systemd/{path.name} still has {leftover}")


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
