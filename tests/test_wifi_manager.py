"""Tests for Wi‑Fi manager helpers."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.wifi_manager import (
    _parse_wifi_list_line,
    connection_has_usable_profile,
    humanize_connect_error,
    scan_wifi,
)


class WifiManagerTests(unittest.TestCase):
    def test_parse_wifi_list_line_bssid_first(self) -> None:
        parsed = _parse_wifi_list_line(r"E0\:63\:DA\:EA\:B2\:6D:Potato 2.4:89:WPA2:")
        self.assertIsNotNone(parsed)
        bssid_raw, ssid, signal, security, in_use = parsed
        self.assertEqual(bssid_raw, r"E0\:63\:DA\:EA\:B2\:6D")
        self.assertEqual(ssid, "Potato 2.4")
        self.assertEqual(signal, "89")
        self.assertEqual(security, "WPA2")

    def test_humanize_wrong_password(self) -> None:
        msg = humanize_connect_error(
            "Error: Connection activation failed: Secrets were required, but not provided.",
            had_password=True,
        )
        self.assertEqual(msg, "Wrong password — check and try again")

    def test_humanize_saved_profile_needs_password(self) -> None:
        msg = humanize_connect_error(
            "Error: Connection activation failed: Secrets were required, but not provided.",
            had_password=False,
        )
        self.assertEqual(msg, "Enter the network password")
        msg = humanize_connect_error(
            "Error: No network with SSID 'Cafe' found.",
            had_password=True,
        )
        self.assertEqual(msg, "Network not in range — go back, Refresh, and try again")

    @mock.patch("patch_browser.wifi_manager.known_connection_names", return_value={"Potato"})
    @mock.patch("patch_browser.wifi_manager._run_nmcli")
    def test_usable_saved_profile_requires_psk(self, run_mock: mock.Mock, _known: mock.Mock) -> None:
        run_mock.return_value = mock.Mock(returncode=0, stdout="\n", stderr="")
        self.assertFalse(connection_has_usable_profile("Potato", secured=True))
        run_mock.return_value = mock.Mock(returncode=0, stdout="goodpassword\n", stderr="")
        self.assertTrue(connection_has_usable_profile("Potato", secured=True))

    @mock.patch("patch_browser.wifi_manager.connection_has_usable_profile", return_value=True)
    @mock.patch("patch_browser.wifi_manager._run_nmcli")
    def test_scan_wifi_falls_back_to_sudo(self, run_mock: mock.Mock, _saved: mock.Mock) -> None:
        scan_stdout = (
            r"E0\:63\:DA\:EA\:B2\:6D:Potato 2.4:70:WPA2:*" + "\n"
            r"18\:E8\:29\:51\:70\:52:Potato 2.4:55:WPA2:" + "\n"
            r"AA\:BB\:CC\:DD\:EE\:FF:Cafe:40:WPA2:" + "\n"
        )

        def side_effect(args, *, timeout, use_sudo=False):
            if args[:2] == ["-t", "-f"] and "--rescan" in args:
                if use_sudo:
                    return mock.Mock(returncode=0, stdout=scan_stdout, stderr="")
                return mock.Mock(returncode=0, stdout=r"E0\:63\:DA\:EA\:B2\:6D:Potato 2.4:70:WPA2:*\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="fail")

        run_mock.side_effect = side_effect
        networks, error = scan_wifi()
        self.assertIsNone(error)
        self.assertEqual(len(networks), 3)
        self.assertTrue(any(call.kwargs.get("use_sudo") for call in run_mock.call_args_list))

    @mock.patch("patch_browser.wifi_manager.connection_has_usable_profile", return_value=True)
    @mock.patch("patch_browser.wifi_manager._run_nmcli")
    def test_scan_wifi_parses_bssid_and_dedupes(self, run_mock: mock.Mock, _saved: mock.Mock) -> None:
        def side_effect(args, *, timeout, use_sudo=False):
            if args[:2] == ["-t", "-f"] and "--rescan" in args:
                return mock.Mock(
                    returncode=0,
                    stdout=(
                        r"E0\:63\:DA\:EA\:B2\:6D:Potato 2.4:70:WPA2:*" + "\n"
                        r"18\:E8\:29\:51\:70\:52:Potato 2.4:55:WPA2:" + "\n"
                        r"AA\:BB\:CC\:DD\:EE\:FF:DIRECT-9A-HP DeskJet 2800 series:40:WPA2:" + "\n"
                        "11:22:33:44:55:66:Cafe:40:WPA2:\n"
                    ),
                    stderr="",
                )
            return mock.Mock(returncode=1, stdout="", stderr="fail")

        run_mock.side_effect = side_effect
        networks, error = scan_wifi()
        self.assertIsNone(error)
        self.assertEqual(len(networks), 3)
        potato = next(n for n in networks if n.ssid == "Potato 2.4")
        self.assertTrue(potato.in_use)
        self.assertTrue(potato.saved)
        self.assertEqual(potato.signal, 70)
        self.assertEqual(potato.bssid, "E0:63:DA:EA:B2:6D")


if __name__ == "__main__":
    unittest.main()
