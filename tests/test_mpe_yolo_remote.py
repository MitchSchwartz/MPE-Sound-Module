"""Tests for the appliance's forced-command wrapper.

The wrapper is the complete definition of what the remote agent can do to the
appliance (docs/racknerd-pi-access-spec.md §Layer weight). It has been asserted
to be the whole boundary since rev 1 while being verified only by hand-typed ssh
commands, which is the wrong way round. These tests close that.

They run the script directly with SSH_ORIGINAL_COMMAND set, so they exercise the
real dispatch path without needing SSH or the appliance. Read-only tokens that
depend on appliance state are checked for *acceptance*, not output — a laptop has
no jackd.
"""

import os
import subprocess
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "scripts" / "pi" / "mpe-yolo-remote.sh"

REJECT_CODE = 2

READ_TOKENS = [
    "ping", "version", "status", "sysinfo", "diagnose",
    "jack-status", "osc-check", "help", "tokens",
    "logs-surge", "logs-jackd", "logs-looper",
    "logs-watchdog", "logs-governor", "logs-midiclock",
]


def run(command, stdin=b"", extra_env=None):
    env = dict(os.environ)
    env["SSH_CLIENT"] = "100.80.219.21 12345 22"
    if command is None:
        env.pop("SSH_ORIGINAL_COMMAND", None)
    else:
        env["SSH_ORIGINAL_COMMAND"] = command
    # Keep the appliance log out of the test run.
    env["LOGFILE"] = "/dev/null"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(WRAPPER)],
        input=stdin, env=env, capture_output=True, timeout=60,
    )


class TestShellIsUnreachable(unittest.TestCase):
    """The whole point of a forced command: no interactive shell, ever."""

    def test_empty_command_rejected(self):
        r = run(None)
        self.assertEqual(r.returncode, REJECT_CODE)

    def test_empty_string_command_rejected(self):
        r = run("")
        self.assertEqual(r.returncode, REJECT_CODE)


class TestStdinIsDiscarded(unittest.TestCase):
    """mpe-cli's remote path sends `bash -s` with the payload on stdin.

    If the wrapper ever read stdin, that payload would execute. It must not,
    and `bash -s` must be rejected as a token like anything else.
    """

    def test_bash_dash_s_is_not_a_token(self):
        r = run("bash -s", stdin=b"id > /tmp/pwned\n")
        self.assertEqual(r.returncode, REJECT_CODE)

    def test_payload_on_stdin_with_valid_token_is_ignored(self):
        r = run("ping", stdin=b"id > /tmp/pwned_via_stdin\n")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("uid=", r.stdout.decode())
        self.assertFalse(Path("/tmp/pwned_via_stdin").exists())


class TestTokensAreFullStringMatches(unittest.TestCase):
    """No prefix matching, no argument parsing, no command injection."""

    def test_all_read_tokens_accepted(self):
        for token in READ_TOKENS:
            with self.subTest(token=token):
                self.assertEqual(run(token).returncode, 0)

    def test_prefix_is_not_enough(self):
        for token in ["pin", "stat", "log", "logs-", "diagnos"]:
            with self.subTest(token=token):
                self.assertEqual(run(token).returncode, REJECT_CODE)

    def test_suffix_and_arguments_rejected(self):
        for token in [
            "ping; id",
            "ping && id",
            "ping | id",
            "ping -n 5",
            "status extra",
            "logs-surge 500",
            "ping\nid",
        ]:
            with self.subTest(token=token):
                self.assertEqual(run(token).returncode, REJECT_CODE)

    def test_shell_metacharacters_do_not_execute(self):
        marker = Path("/tmp/mpe_yolo_injection_marker")
        marker.unlink(missing_ok=True)
        for token in [
            f"ping; touch {marker}",
            f"$(touch {marker})",
            f"`touch {marker}`",
            f"ping $(touch {marker})",
        ]:
            with self.subTest(token=token):
                run(token)
                self.assertFalse(
                    marker.exists(), f"token executed a subcommand: {token!r}"
                )


class TestDangerousCommandsRejected(unittest.TestCase):
    """Things the agent must never be able to ask for."""

    def test_shell_and_admin_commands_rejected(self):
        for token in [
            "bash", "sh", "/bin/bash", "bash -i", "sudo -s", "su -",
            "systemctl restart surge-xt-cli",
            "systemctl stop mpe-jackd",
            "rm -rf /",
            "cat /etc/shadow",
            "cat /etc/mpe/mpe.env",
            "git -C /home/mitch/MPE-Module pull",
            "scp /etc/passwd elsewhere:",
            "python3 -c 'import os; os.system(\"id\")'",
            "deploy-all.sh",
            "reboot",
            "shutdown -h now",
        ]:
            with self.subTest(token=token):
                self.assertEqual(run(token).returncode, REJECT_CODE)


class TestNoRepoDependency(unittest.TestCase):
    """The wrapper must not run code from the agent-writable checkout.

    The appliance's repo is writable by mitch, and agent-authored code reaches
    it (see the spec's Decision C). If the wrapper called scripts from there,
    the agent would choose the code the wrapper runs.
    """

    def test_wrapper_does_not_invoke_repo_scripts(self):
        text = WRAPPER.read_text()
        for needle in ["MPE-Module", "scripts/lib", "paths.sh", "$REPO"]:
            self.assertNotIn(
                needle, text.split("# Read-only token set.")[-1],
                f"wrapper dispatch references the repo checkout: {needle}",
            )

    def test_stdin_is_discarded_before_any_dispatch(self):
        lines = [
            l.strip() for l in WRAPPER.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        self.assertIn("exec </dev/null", lines[:3],
                      "stdin must be discarded in the first executable lines")


if __name__ == "__main__":
    unittest.main()
