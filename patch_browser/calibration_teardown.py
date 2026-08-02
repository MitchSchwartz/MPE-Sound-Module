"""Shared cleanup after calibration run or cancel (Surge, snd-aloop, systemd).

Browser handoff invariant (``MPE_CALIB_FROM_BROWSER=1``):

- Do not stop ``touch-patch-browser`` during ``stop_mpe_audio_services`` — the loader
  replaces the browser process via ``exec``; stopping the unit kills teardown.
- Do not ``systemctl restart touch-patch-browser`` from the loader — ``exec`` back
  into ``touch_patch_browser.py`` instead (same service PID chain, no crash loop).

See ``patch_browser.calibration_constants`` for the env var name and helper.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from patch_browser.calibration_constants import (
    MPE_CALIB_FROM_BROWSER,
    TOUCH_PATCH_BROWSER_SCRIPT,
    calibration_from_browser,
)


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
    """Stop production Surge (and patch browser unless launched from browser exec handoff)."""
    units: list[str] = []
    if not calibration_from_browser():
        units.append("touch-patch-browser")
    units.append("surge-xt-cli")
    for unit in units:
        subprocess.run(["sudo", "systemctl", "stop", unit], check=False)
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
    subprocess.run(["sudo", "systemctl", "start", "surge-xt-cli"], check=False)
    if restart_browser and not calibration_from_browser():
        subprocess.run(["sudo", "systemctl", "start", "touch-patch-browser"], check=False)
