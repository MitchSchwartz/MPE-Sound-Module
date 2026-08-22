#!/usr/bin/env python3
"""CPU-aware Surge poly limit governor (standalone daemon)."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.surge_cpu_monitor import SurgeCpuMonitor  # noqa: E402
from patch_browser.surge_monitor import SurgeMonitor  # noqa: E402
from patch_browser.surge_poly_governor import SurgePolyGovernor  # noqa: E402


class _Stop:
    def __init__(self) -> None:
        self.flag = False
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, self._handle)

    def _handle(self, _signum: int, _frame: object) -> None:
        self.flag = True


def main() -> int:
    try:
        from pythonosc import udp_client
    except ImportError:
        print("Error: python-osc required for surge-poly-governor", file=sys.stderr)
        return 1

    monitor = SurgeMonitor()
    cpu_monitor = SurgeCpuMonitor(monitor)
    cpu_monitor.start()
    osc = udp_client.SimpleUDPClient("127.0.0.1", 53280)
    stop = _Stop()
    governor = SurgePolyGovernor(osc, surge_monitor=monitor, cpu_monitor=cpu_monitor)
    governor.start()
    print("Surge poly governor running.", flush=True)
    try:
        while not stop.flag:
            # Sleep in slices so SIGTERM is honoured promptly rather than at interval edge.
            time.sleep(0.2)
    finally:
        governor.stop()
        cpu_monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
