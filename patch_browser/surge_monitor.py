"""Surge XT CLI process health monitoring (no GPIO / OLED dependencies)."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

OSC_PORT = 53280


from patch_browser.audio_engine import read_engine_state


class SurgeMonitor:
    """Monitors Surge XT CLI process health and provides restart capability."""

    def __init__(self, log_file=None):
        if log_file is None:
            log_file = os.environ.get(
                "MPE_SURGE_LOG", os.path.expanduser("~/surge-cli.log")
            )
        self.log_file = Path(log_file)
        self.surge_pid = None
        self.last_check_time = 0
        self.check_interval = 2.0
        self.is_healthy = False
        self.last_error = None
        self.startup_time = time.time()
        self._find_surge_process()

    def _find_surge_process(self):
        """Refresh cached PID from a running surge-xt-cli instance."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", r"surge-xt-cli.*--osc-in-port"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode != 0 or not result.stdout.strip():
                result = subprocess.run(
                    ["pgrep", "-f", "surge-xt-cli"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
            if result.returncode == 0 and result.stdout.strip():
                pids = [int(pid) for pid in result.stdout.strip().split() if pid.isdigit()]
                if pids:
                    self.surge_pid = pids[0]
                    self.is_healthy = True
                    self.last_error = None
                    return
            self.surge_pid = None
            self.is_healthy = False
            if self.last_error is None:
                self.last_error = "Surge not running"
        except Exception as e:
            print(f"Error finding Surge process: {e}")
            self.surge_pid = None
            self.is_healthy = False
            self.last_error = str(e)

    def _pid_is_alive(self, pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def osc_port_in_use(self) -> bool:
        """Return True when something is bound to Surge's OSC input port."""
        return self._is_osc_port_in_use()

    def _is_osc_port_in_use(self) -> bool:
        """Return True when something is bound to Surge's OSC input port."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.2)
                sock.bind(("127.0.0.1", OSC_PORT))
                return False
        except OSError:
            return True
        except Exception:
            pass

        for cmd in (["ss", "-ulnp"], ["netstat", "-uln"]):
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=1
                )
                if result.returncode == 0 and str(OSC_PORT) in result.stdout:
                    return True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return False

    def _surge_is_live(self) -> tuple[bool, str | None]:
        """True when the running instance responds via process and/or OSC."""
        self._find_surge_process()
        pid_ok = self._pid_is_alive(self.surge_pid)
        if not pid_ok:
            self.surge_pid = None

        osc_ok = self._is_osc_port_in_use()
        if pid_ok or osc_ok:
            if osc_ok and not pid_ok:
                self._find_surge_process()
            return True, None

        error = self._get_last_error_from_log() or "Surge not running"
        return False, error

    def check_health(self):
        current_time = time.time()
        if current_time - self.last_check_time < self.check_interval:
            return self.is_healthy, self.last_error
        self.last_check_time = current_time

        if current_time - self.startup_time < 5.0:
            self.is_healthy = True
            self.last_error = None
            return True, None

        live, error = self._surge_is_live()
        self.is_healthy = live
        self.last_error = error if not live else None
        return self.is_healthy, self.last_error

    def _get_last_error_from_log(self):
        try:
            if not self.log_file.exists():
                return None
            result = subprocess.run(
                ["tail", "-50", str(self.log_file)],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode != 0:
                return None
            lines = result.stdout.strip().split("\n")
            for line in reversed(lines):
                line_lower = line.lower()
                if any(
                    keyword in line_lower
                    for keyword in ["error", "fatal", "crash", "failed", "unable"]
                ):
                    if "unable to open audio device" in line_lower:
                        continue
                    if "error" in line_lower:
                        parts = line.split("Error:", 1)
                        if len(parts) > 1:
                            return parts[1].strip()[:50]
                    return line.strip()[:50]
            return None
        except Exception as e:
            print(f"Error reading Surge log: {e}")
            return None

    def _graph_failure_blocks_restart(self) -> tuple[bool, str | None]:
        state = read_engine_state()
        if state.get("state") != "failed":
            return False, None
        reason = state.get("reason", "")
        if reason in ("no-server", "no-jack-device"):
            return True, "Fix DAC / jackd first — audio graph unavailable"
        return False, None

    def restart_surge(self):
        blocked, message = self._graph_failure_blocks_restart()
        if blocked:
            return False, message or "Audio graph unavailable"
        try:
            print("Attempting to restart Surge XT CLI...")
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "surge-xt-cli.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                time.sleep(3)
                live, error = self._surge_is_live()
                if live:
                    return True, "Surge restarted"
                return False, error or "Restart failed - check logs"
            return False, f"systemctl error: {result.stderr[:30]}"
        except subprocess.TimeoutExpired:
            return False, "Restart timeout"
        except Exception as e:
            return False, f"Error: {str(e)[:30]}"

    def get_status_summary(self):
        is_healthy, error = self.check_health()
        blocked, block_reason = self._graph_failure_blocks_restart()
        if is_healthy:
            if self.surge_pid:
                details = f"PID {self.surge_pid}"
            else:
                details = f"OSC :{OSC_PORT}"
            return {
                "status": "Running",
                "details": details,
                "can_restart": False,
            }
        return {
            "status": "Not Running",
            "details": block_reason or error or "Unknown error",
            "can_restart": not blocked,
        }
