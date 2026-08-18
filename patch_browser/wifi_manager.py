"""NetworkManager Wi‑Fi helpers for the touch appliance (nmcli, no shell)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

SCAN_TIMEOUT_S = 25.0
CONNECT_TIMEOUT_S = 45.0


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    secured: bool
    in_use: bool
    saved: bool
    bssid: str | None = None


def _unescape_nmcli(value: str) -> str:
    return value.replace("\\:", ":")


def _run_nmcli(
    args: list[str],
    *,
    timeout: float,
    use_sudo: bool = False,
) -> subprocess.CompletedProcess[str]:
    prefix = ["sudo", "-n", "nmcli"] if use_sudo else ["nmcli"]
    return subprocess.run(
        [*prefix, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def known_connection_names() -> set[str]:
    result = _run_nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"], timeout=10.0)
    if result.returncode != 0:
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        name, conn_type = parts[0], parts[1]
        if conn_type == "802-11-wireless" and name != "lo":
            names.add(name)
    return names


def connection_has_usable_profile(name: str, *, secured: bool) -> bool:
    """True when NM has a profile that can reconnect without prompting.

    Broken profiles from failed joins often exist but have no stored PSK —
    those should not show as saved in the UI.
    """
    if name not in known_connection_names():
        return False
    if not secured:
        return True
    result = _run_nmcli(
        ["-s", "-g", "802-11-wireless-security.psk", "connection", "show", name],
        timeout=10.0,
    )
    if result.returncode != 0:
        return False
    psk = result.stdout.strip()
    return len(psk) >= 8


def current_wifi_label() -> str:
    result = _run_nmcli(["-t", "-f", "active,ssid", "dev", "wifi"], timeout=10.0)
    if result.returncode != 0:
        return "Unavailable"
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        active, ssid = (line.split(":", 1) + [""])[:2]
        if active == "yes" and ssid:
            return ssid
    return "Not connected"


def _parse_wifi_list_line(line: str) -> tuple[str, str, str, str, str] | None:
    """Parse BSSID, SSID, SIGNAL, SECURITY, IN-USE from nmcli -t."""
    parts = line.rsplit(":", 4)
    if len(parts) != 5:
        return None
    bssid_raw, ssid, signal_raw, security, in_use = parts
    return bssid_raw, ssid, signal_raw, security, in_use


def _parse_bssid_ssid_signal(line: str) -> tuple[str, str, int] | None:
    parts = line.rsplit(":", 2)
    if len(parts) != 3:
        return None
    bssid_raw, ssid, signal_raw = parts
    try:
        signal = int(signal_raw)
    except ValueError:
        signal = 0
    return _unescape_nmcli(bssid_raw), ssid, signal


def resolve_bssid(ssid: str, *, rescan: bool = True) -> str | None:
    """Find the strongest AP for an SSID (optional rescan first)."""
    args = ["-t", "-f", "BSSID,SSID,SIGNAL", "dev", "wifi", "list"]
    if rescan:
        args.append("--rescan")
        args.append("yes")
    result = _run_nmcli(args, timeout=SCAN_TIMEOUT_S)
    if result.returncode != 0:
        return None

    best_bssid: str | None = None
    best_signal = -1
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parsed = _parse_bssid_ssid_signal(line)
        if parsed is None or parsed[1] != ssid:
            continue
        bssid, _, signal = parsed
        if signal > best_signal:
            best_signal = signal
            best_bssid = bssid
    return best_bssid


def _parse_wifi_scan_output(stdout: str) -> list[WifiNetwork]:
    merged: dict[str, WifiNetwork] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parsed = _parse_wifi_list_line(line)
        if parsed is None:
            continue
        bssid_raw, ssid, signal_raw, security, in_use = parsed
        if not ssid or ssid == "--":
            continue
        try:
            signal = int(signal_raw)
        except ValueError:
            signal = 0
        secured = security not in ("", "--")
        active = in_use == "*"
        saved_profile = connection_has_usable_profile(ssid, secured=secured)
        bssid = _unescape_nmcli(bssid_raw) if bssid_raw else None
        candidate = WifiNetwork(
            ssid=ssid,
            signal=signal,
            secured=secured,
            in_use=active,
            saved=saved_profile,
            bssid=bssid,
        )
        existing = merged.get(ssid)
        if existing is None or candidate.signal > existing.signal or candidate.in_use:
            merged[ssid] = candidate
    return sorted(
        merged.values(),
        key=lambda n: (not n.in_use, not n.saved, -n.signal, n.ssid.lower()),
    )


def _wifi_list_rescan(*, use_sudo: bool) -> subprocess.CompletedProcess[str]:
    return _run_nmcli(
        [
            "-t",
            "-f",
            "BSSID,SSID,SIGNAL,SECURITY,IN-USE",
            "dev",
            "wifi",
            "list",
            "--rescan",
            "yes",
        ],
        timeout=SCAN_TIMEOUT_S,
        use_sudo=use_sudo,
    )


def scan_wifi() -> tuple[list[WifiNetwork], str | None]:
    # `--rescan yes` is required — plain `dev wifi list` only returns cached APs
    # (often just the connected network). Retry with sudo when the netdev scan
    # returns one AP or fails (Pi OS / polkit variance).
    result = _wifi_list_rescan(use_sudo=False)
    error: str | None = None
    networks: list[WifiNetwork] = []
    if result.returncode == 0:
        networks = _parse_wifi_scan_output(result.stdout)
    else:
        detail = (result.stderr or result.stdout or "scan failed").strip()
        error = detail.splitlines()[0][:80]

    if result.returncode != 0 or len(networks) <= 1:
        sudo_result = _wifi_list_rescan(use_sudo=True)
        if sudo_result.returncode == 0:
            sudo_networks = _parse_wifi_scan_output(sudo_result.stdout)
            if len(sudo_networks) > len(networks):
                return sudo_networks, None
            if sudo_networks:
                return sudo_networks, None
        if sudo_result.returncode != 0 and not networks:
            detail = (sudo_result.stderr or sudo_result.stdout or "scan failed").strip()
            return [], detail.splitlines()[0][:80]

    if result.returncode != 0:
        return [], error
    return networks, None


def connect_failure_needs_password(message: str) -> bool:
    """True when the UI should prompt for a password after a failed join."""
    return message in {
        "Wrong password — check and try again",
        "Enter the network password",
    }


def humanize_connect_error(detail: str, *, had_password: bool) -> str:
    """Map nmcli/NM noise to touch-friendly messages."""
    text = detail.strip()
    lower = text.lower()
    if had_password:
        if "no network with ssid" in lower:
            return "Network not in range — go back, Refresh, and try again"
        if any(
            phrase in lower
            for phrase in (
                "no-secrets",
                "no agents were available",
                "secrets were required",
                "supplicant-disconnect",
                "4way_handshake",
                "802-11-wireless-security.key-mgmt",
                "authentication failed",
                "incorrect password",
                "pre-shared key",
            )
        ):
            return "Wrong password — check and try again"
    elif any(
        phrase in lower
        for phrase in (
            "no-secrets",
            "no agents were available",
            "secrets were required",
        )
    ):
        return "Enter the network password"
    if "not authorized" in lower:
        return "Could not join network (permissions)"
    first = text.splitlines()[0][:80]
    if first.lower().startswith("error:"):
        first = first[6:].strip()
    return first or "Connect failed"


def _delete_saved_connection(ssid: str) -> None:
    forget_wifi(ssid)


def forget_wifi(ssid: str) -> tuple[bool, str]:
    """Remove a saved Wi‑Fi profile from NetworkManager."""
    if not ssid:
        return False, "Missing network name"
    if ssid not in known_connection_names():
        return False, "Network not saved"
    result = _run_nmcli(["connection", "delete", ssid], timeout=10.0, use_sudo=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "forget failed").strip()
        first = detail.splitlines()[0][:80]
        if first.lower().startswith("error:"):
            first = first[6:].strip()
        return False, first or "Could not forget network"
    return True, f"Forgot {ssid}"


def connect_wifi(
    ssid: str,
    password: str | None = None,
    *,
    bssid: str | None = None,
) -> tuple[bool, str]:
    if not ssid:
        return False, "Missing network name"

    saved = ssid in known_connection_names()
    had_password = bool(password)

    if password:
        # Headless Pi has no NM secrets agent — password joins need sudo, a fresh
        # rescan for BSSID, and clearing any broken saved profile from a prior try.
        target_bssid = bssid or resolve_bssid(ssid, rescan=True)
        if not target_bssid:
            return False, humanize_connect_error(
                "No network with SSID found",
                had_password=True,
            )
        _delete_saved_connection(ssid)
        result = _run_nmcli(
            [
                "dev",
                "wifi",
                "connect",
                ssid,
                "password",
                password,
                "ifname",
                "wlan0",
                "bssid",
                target_bssid,
            ],
            timeout=CONNECT_TIMEOUT_S,
            use_sudo=True,
        )
    elif saved:
        result = _run_nmcli(["connection", "up", ssid], timeout=CONNECT_TIMEOUT_S, use_sudo=True)
    else:
        target_bssid = bssid or resolve_bssid(ssid, rescan=True)
        if not target_bssid:
            return False, humanize_connect_error(
                "No network with SSID found",
                had_password=False,
            )
        args = ["dev", "wifi", "connect", ssid, "ifname", "wlan0", "bssid", target_bssid]
        result = _run_nmcli(args, timeout=CONNECT_TIMEOUT_S, use_sudo=True)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "connect failed").strip()
        return False, humanize_connect_error(detail, had_password=had_password)
    return True, f"Connected to {ssid}"


def wifi_settings_row_label() -> str:
    label = current_wifi_label()
    if label == "Not connected":
        return "Wi‑Fi — Not connected"
    if label == "Unavailable":
        return "Wi‑Fi — unavailable"
    return f"Wi‑Fi — {label}"
