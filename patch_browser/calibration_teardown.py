"""Shared cleanup after calibration run or cancel (Surge, snd-aloop, systemd).

Browser handoff invariant (``MPE_CALIB_FROM_BROWSER=1``):

- Do not stop ``touch-patch-browser`` during ``stop_mpe_audio_services`` — the loader
  replaces the browser process via ``exec``; stopping the unit kills teardown.
- Do not ``systemctl restart touch-patch-browser`` from the loader — ``exec`` back
  into ``touch_patch_browser.py`` instead (same service PID chain, no crash loop).

See ``patch_browser.calibration_constants`` for the env var name and helper.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from collections.abc import Iterator

from patch_browser.calibration_constants import (
    MPE_CALIB_FROM_BROWSER,
    TOUCH_PATCH_BROWSER_SCRIPT,
    calibration_from_browser,
)
from patch_browser.session_events import emit_event
from patch_browser.session_snapshot import (
    clear_maintenance_flag,
    maintenance_mode_active,
    set_maintenance_flag,
    systemd_unit_active,
)

# Looper eval stack — Restart=always units (spec Phase 0 / Appendix A).
# Stop in reverse dependency order; start after Surge is back.
LOOPER_UNITS_STOP_ORDER = (
    "mpe-apc-bench",
    "sl-hud-monitor",
    "sl-watchdog",
    "mpe-sooperlooper",
)
LOOPER_UNITS_START_ORDER = (
    "mpe-sooperlooper",
    "mpe-apc-bench",
    "sl-hud-monitor",
    "sl-watchdog",
)


def _systemctl(unit: str, verb: str) -> None:
    subprocess.run(["sudo", "systemctl", verb, f"{unit}.service"], check=False)


def _safe_emit_event(name: str, **kwargs: object) -> None:
    try:
        emit_event(name, **kwargs)  # type: ignore[arg-type]
    except (OSError, ValueError):
        pass


def ensure_looper_units_running() -> None:
    """Start looper units left stopped by an aborted calibration (no maintenance flag)."""
    if maintenance_mode_active():
        return
    for unit in LOOPER_UNITS_START_ORDER:
        if systemd_unit_active(unit) is False:
            _systemctl(unit, "start")


def unload_snd_aloop_if_idle() -> None:
    """Remove ALSA loopback when nothing holds a reference."""
    try:
        with open("/proc/modules") as modules:
            for line in modules:
                if line.startswith("snd_aloop "):
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == "0":
                        subprocess.run(["sudo", "modprobe", "-r", "snd_aloop"], check=False)
                    return
    except OSError:
        pass


def stop_mpe_audio_services() -> None:
    """Stop production Surge, pressure remapper, patch browser, and looper stack."""
    set_maintenance_flag(source="calibration")
    units: list[str] = []
    if not calibration_from_browser():
        units.append("touch-patch-browser")
    units.extend(["mpe-pressure-remap", "surge-poly-governor", "surge-xt-cli"])
    units.extend(LOOPER_UNITS_STOP_ORDER)
    for unit in units:
        _systemctl(unit, "stop")
    _safe_emit_event(
        "looper.units.stopped",
        detail="calibration",
        source="calibration_teardown.py",
        fields={"units": ",".join(LOOPER_UNITS_STOP_ORDER)},
    )
    _safe_emit_event("mode.changed", detail="calibration-stop", source="calibration_teardown.py")
    time.sleep(1)
    subprocess.run(["sudo", "pkill", "-f", "surge-xt-cli"], check=False)
    time.sleep(0.5)


def exec_touch_patch_browser() -> None:
    """Replace loader process with the touch browser (browser-initiated cal only)."""
    if not TOUCH_PATCH_BROWSER_SCRIPT.is_file():
        raise RuntimeError(f"Touch patch browser not found: {TOUCH_PATCH_BROWSER_SCRIPT}")
    os.environ.pop(MPE_CALIB_FROM_BROWSER, None)
    os.execv(
        sys.executable,
        [sys.executable, "-u", str(TOUCH_PATCH_BROWSER_SCRIPT)],
    )


def restore_mpe_audio_services(*, restart_browser: bool = True) -> None:
    """Stop calibration Surge, unload loopback, restart production services."""
    subprocess.run(["sudo", "pkill", "-f", "surge-xt-cli"], check=False)
    time.sleep(0.5)
    unload_snd_aloop_if_idle()
    _systemctl("mpe-pressure-remap", "start")
    _systemctl("surge-poly-governor", "start")
    _systemctl("surge-xt-cli", "start")
    time.sleep(1)
    for unit in LOOPER_UNITS_START_ORDER:
        _systemctl(unit, "start")
    _safe_emit_event(
        "looper.units.started",
        detail="calibration-restore",
        source="calibration_teardown.py",
        fields={"units": ",".join(LOOPER_UNITS_START_ORDER)},
    )
    _safe_emit_event("mode.changed", detail="calibration-restore", source="calibration_teardown.py")
    if restart_browser and not calibration_from_browser():
        _systemctl("touch-patch-browser", "start")
    clear_maintenance_flag()


@contextlib.contextmanager
def calibration_audio_scope(*, restart_browser: bool = True, restore: bool = True) -> Iterator[None]:
    """Stop production stack for calibration; always restore or clear maintenance on exit."""
    stop_mpe_audio_services()
    try:
        yield
    finally:
        if restore:
            restore_mpe_audio_services(restart_browser=restart_browser)
        else:
            clear_maintenance_flag()
