"""Shared cleanup after calibration run or cancel (Surge, snd-aloop, systemd).

Browser handoff invariant (``MPE_CALIB_FROM_BROWSER=1``):

- Do not stop ``touch-patch-browser`` during ``stop_mpe_audio_services`` — the loader
  replaces the browser process via ``exec``; stopping the unit kills teardown.
- On restore, schedule ``systemctl restart touch-patch-browser`` asynchronously instead
  of a blocking ``systemctl start`` (same-process deadlock otherwise).

See ``patch_browser.calibration_constants`` for the env var name and helper.
"""

from __future__ import annotations

import subprocess
import time

from patch_browser.calibration_constants import calibration_from_browser


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
    subprocess.run(["pkill", "-f", "surge-xt-cli"], check=False)
    time.sleep(0.5)


def _schedule_touch_browser_restart() -> None:
    """Restart browser after this process exits (avoids systemd stop deadlock)."""
    subprocess.Popen(
        [
            "sudo",
            "bash",
            "-c",
            "sleep 2; systemctl restart touch-patch-browser",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def restore_mpe_audio_services(*, restart_browser: bool = True) -> None:
    """Stop calibration Surge, unload loopback, restart production services."""
    subprocess.run(["pkill", "-f", "surge-xt-cli"], check=False)
    time.sleep(0.5)
    unload_snd_aloop_if_idle()
    subprocess.run(["sudo", "systemctl", "start", "surge-xt-cli"], check=False)
    if restart_browser:
        if calibration_from_browser():
            # Loader runs as the service main process; synchronous restart deadlocks
            # stop (this process) with teardown still in finally.
            _schedule_touch_browser_restart()
        else:
            subprocess.run(["sudo", "systemctl", "start", "touch-patch-browser"], check=False)
