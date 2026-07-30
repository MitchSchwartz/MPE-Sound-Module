"""Surge XT CLI process health monitoring (no GPIO / OLED dependencies)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


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
        try:
            result = subprocess.run(
                ["pgrep", "-f", "surge-xt-cli"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode == 0 and result.stdout.strip():
                self.surge_pid = int(result.stdout.strip().split()[0])
                self.is_healthy = True
                print(f"Found existing Surge process: PID {self.surge_pid}")
            else:
                self.is_healthy = False
                self.last_error = "Surge not running"
        except Exception as e:
            print(f"Error finding Surge process: {e}")
            self.is_healthy = False
            self.last_error = str(e)

    def check_health(self):
        current_time = time.time()
        if current_time - self.last_check_time < self.check_interval:
            return self.is_healthy, self.last_error
        self.last_check_time = current_time
        if current_time - self.startup_time < 5.0:
            return True, None
        if self.surge_pid is not None:
            try:
                os.kill(self.surge_pid, 0)
                self.is_healthy = True
                self.last_error = None
                return True, None
            except OSError:
                self.is_healthy = False
                self.last_error = "Surge crashed or exited"
                error_detail = self._get_last_error_from_log()
                if error_detail:
                    self.last_error = error_detail
                return False, self.last_error
        self._find_surge_process()
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
                    if "error" in line_lower:
                        parts = line.split("Error:", 1)
                        if len(parts) > 1:
                            return parts[1].strip()[:50]
                    return line.strip()[:50]
            return None
        except Exception as e:
            print(f"Error reading Surge log: {e}")
            return None

    def restart_surge(self):
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
                self._find_surge_process()
                if self.is_healthy:
                    return True, "Surge restarted"
                return False, "Restart failed - check logs"
            return False, f"systemctl error: {result.stderr[:30]}"
        except subprocess.TimeoutExpired:
            return False, "Restart timeout"
        except Exception as e:
            return False, f"Error: {str(e)[:30]}"

    def get_status_summary(self):
        is_healthy, error = self.check_health()
        if is_healthy:
            return {
                "status": "Running",
                "details": f"PID {self.surge_pid}",
                "can_restart": False,
            }
        return {
            "status": "Not Running",
            "details": error or "Unknown error",
            "can_restart": True,
        }
