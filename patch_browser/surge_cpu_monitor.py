"""Surge CPU load sampling for the touch patch browser header meter."""

from __future__ import annotations

import os
import socket
import threading
import time

OSC_IN_PORT = 53280
OSC_OUT_PORT = 53270
OSC_CPU_QUERY_PATHS = ("/q/cpu", "/cpu", "/status/cpu")
POLL_INTERVAL_S = 0.2  # 5 Hz
CPU_FALLOFF = 0.92


class SurgeCpuMonitor:
    """Background sampler for Surge engine load shown in the touch UI header.

    Surge XT's GUI VU meter uses an internal audio-callback ratio (`cpu_level`).
    Upstream Surge does not expose that value via OSC (no `/q/cpu` in the spec).
    We poll speculative OSC query paths when OSC out is enabled, then fall back to
    `/proc` CPU time for the `surge-xt-cli` process (good Pi diagnostic proxy).
    """

    def __init__(
        self,
        surge_monitor,
        *,
        poll_interval: float = POLL_INTERVAL_S,
        osc_in_port: int = OSC_IN_PORT,
        osc_out_port: int = OSC_OUT_PORT,
    ) -> None:
        self.surge_monitor = surge_monitor
        self.poll_interval = poll_interval
        self.osc_in_port = osc_in_port
        self.osc_out_port = osc_out_port
        self._lock = threading.Lock()
        self._percent: float | None = None
        self._online = False
        self._source = "none"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._clk_tck = os.sysconf(os.SC_CLK_TCK)
        self._last_jiffies: tuple[int, float] | None = None
        self._osc_client = None
        self._osc_cpu_supported: bool | None = None
        self._osc_probe_counter = 0
        self._init_osc_client()

    def _init_osc_client(self) -> None:
        try:
            from pythonosc import udp_client

            self._osc_client = udp_client.SimpleUDPClient("127.0.0.1", self.osc_in_port)
        except Exception:
            self._osc_client = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="SurgeCpuMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "online": self._online,
                "percent": self._percent,
                "source": self._source,
            }

    def _worker(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self._poll_once()
            except Exception as exc:
                print(f"Surge CPU monitor poll error: {exc}")

    def _poll_once(self) -> None:
        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            self._last_jiffies = None
            with self._lock:
                self._online = False
                self._percent = None
                self._source = "none"
            return

        self._osc_probe_counter += 1
        if self._osc_cpu_supported is not False and (
            self._osc_cpu_supported is True or self._osc_probe_counter % 25 == 1
        ):
            if self._try_osc_cpu_query():
                return

        pid = self.surge_monitor.surge_pid
        if pid is None or not self._pid_is_alive(pid):
            self.surge_monitor._find_surge_process()
            pid = self.surge_monitor.surge_pid
        if pid is None or not self._pid_is_alive(pid):
            with self._lock:
                self._online = False
                self._percent = None
                self._source = "none"
            return

        percent = self._sample_proc_cpu_percent(pid)
        with self._lock:
            self._online = True
            self._source = "proc"
            if percent is None:
                return
            if self._percent is None:
                self._percent = percent
            else:
                self._percent = max(
                    percent,
                    self._percent * CPU_FALLOFF,
                )

    def _try_osc_cpu_query(self) -> bool:
        if self._osc_client is None:
            return False

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", self.osc_out_port))
            sock.settimeout(0.05)
            for path in OSC_CPU_QUERY_PATHS:
                self._osc_client.send_message(path, [])
                try:
                    data, _addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                percent = self._parse_osc_cpu_payload(data)
                if percent is not None:
                    self._osc_cpu_supported = True
                    with self._lock:
                        self._online = True
                        self._source = "osc"
                        self._percent = percent
                    return True
        except OSError:
            self._osc_cpu_supported = False
            return False
        finally:
            sock.close()
        if self._osc_cpu_supported is None:
            self._osc_cpu_supported = False
        return False

    @staticmethod
    def _parse_osc_cpu_payload(data: bytes) -> float | None:
        if len(data) < 8 or data[0] != 0x2F:
            return None
        # Best-effort: first OSC float32 in the datagram.
        idx = data.find(b"\x00,\x00")
        while idx != -1:
            start = idx + 4
            if start + 4 <= len(data):
                import struct

                val = struct.unpack(">f", data[start : start + 4])[0]
                if val != val:  # NaN
                    return None
                if 0.0 <= val <= 1.0:
                    return val * 100.0
                if 0.0 <= val <= 100.0:
                    return val
                if 0.0 < val <= 200.0:
                    return min(val, 100.0)
            idx = data.find(b"\x00,\x00", idx + 1)
        return None

    def _sample_proc_cpu_percent(self, pid: int) -> float | None:
        try:
            jiffies = self._read_proc_jiffies(pid)
        except OSError:
            self._last_jiffies = None
            return None

        now = time.monotonic()
        if self._last_jiffies is None:
            self._last_jiffies = (jiffies, now)
            return None

        prev_jiffies, prev_time = self._last_jiffies
        delta_jiffies = jiffies - prev_jiffies
        delta_time = now - prev_time
        self._last_jiffies = (jiffies, now)
        if delta_time <= 0.05 or delta_jiffies < 0:
            return None

        cpu_seconds = delta_jiffies / self._clk_tck
        # One core at 100% ~= 100 on the meter (Ableton-style).
        return max(0.0, min(100.0, (cpu_seconds / delta_time) * 100.0))

    def _read_proc_jiffies(self, pid: int) -> int:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            stat = handle.read().decode(errors="ignore").split()
        if len(stat) < 15:
            raise OSError("unexpected /proc stat format")
        return int(stat[13]) + int(stat[14])

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
