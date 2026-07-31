"""Shared cleanup after calibration run or cancel (Surge, snd-aloop, systemd)."""

from __future__ import annotations

import subprocess
import time


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


def restore_mpe_audio_services(*, restart_browser: bool = True) -> None:
    """Stop calibration Surge, unload loopback, restart production services."""
    subprocess.run(["pkill", "-f", "surge-xt-cli"], check=False)
    time.sleep(0.5)
    unload_snd_aloop_if_idle()
    subprocess.run(["sudo", "systemctl", "start", "surge-xt-cli"], check=False)
    if restart_browser:
        subprocess.run(["sudo", "systemctl", "start", "touch-patch-browser"], check=False)
