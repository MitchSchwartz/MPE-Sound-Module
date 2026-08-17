"""Passive JACK peak meter for Surge output (fail-open parallel tap)."""

from __future__ import annotations

import struct
import threading
import time

from patch_browser.peak_meter_math import dbfs_to_meter_ratio, linear_peak_to_dbfs

POLL_INTERVAL_S = 0.2  # 5 Hz — UI reads snapshot only
PEAK_DECAY = 0.86  # per poll tick between peaks
SURGE_JACK_CLIENT = "Surge XT"
METER_JACK_CLIENT = "mpe-peak-meter"
RECONNECT_INTERVAL_S = 2.0


def _buffer_peak(buf, nframes: int) -> float:
    """Max abs sample from a JACK float32 port buffer (no numpy)."""
    if nframes <= 0:
        return 0.0
    peak = 0.0
    mv = memoryview(buf)[: nframes * 4]
    for offset in range(0, len(mv), 4):
        sample = struct.unpack_from("<f", mv, offset)[0]
        if not (sample == sample):  # NaN
            continue
        a = -sample if sample < 0.0 else sample
        if a > peak:
            peak = a
    return peak


class SurgePeakMonitor:
    """Parallel JACK input-only client — never sits in Surge's playback path.

    Topology (fail-open):
      Surge XT:out_{1,2} → system:playback_{1,2}   (unchanged)
      Surge XT:out_{1,2} → mpe-peak-meter:in_{1,2} (optional fan-out)

    No output ports on this client, so it cannot insert downstream of Surge.
    If JACK is down, Surge is offline, or wiring fails, the touch UI shows —.
    """

    def __init__(
        self,
        surge_monitor,
        *,
        poll_interval: float = POLL_INTERVAL_S,
        surge_client: str = SURGE_JACK_CLIENT,
    ) -> None:
        self.surge_monitor = surge_monitor
        self.poll_interval = poll_interval
        self.surge_client = surge_client
        self._lock = threading.Lock()
        self._peak_linear = 0.0
        self._period_peak = 0.0
        self._online = False
        self._source = "none"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._jack = None
        self._client = None
        self._inports: list = []
        self._jack_available: bool | None = None
        self._last_connect_attempt = 0.0
        self._wired = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="SurgePeakMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._shutdown_jack()

    def snapshot(self) -> dict:
        with self._lock:
            if not self._online:
                return {
                    "online": False,
                    "peak_linear": None,
                    "dbfs": None,
                    "ratio": None,
                    "source": self._source,
                }
            dbfs = linear_peak_to_dbfs(self._peak_linear)
            ratio = dbfs_to_meter_ratio(dbfs)
            if ratio is None:
                ratio = 0.0
            return {
                "online": True,
                "peak_linear": self._peak_linear,
                "dbfs": dbfs,
                "ratio": ratio,
                "source": self._source,
            }

    def _worker(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self._poll_once()
            except Exception as exc:
                print(f"Surge peak monitor poll error: {exc}")

    def _poll_once(self) -> None:
        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            self._reset_display(online=False)
            return

        if not self._ensure_jack_client():
            self._reset_display(online=False)
            return

        now = time.monotonic()
        if now - self._last_connect_attempt >= RECONNECT_INTERVAL_S:
            self._last_connect_attempt = now
            self._try_connect_surge_outputs()

        wired = self._surge_outputs_connected()
        with self._lock:
            window_peak = self._period_peak
            self._period_peak = 0.0
            if window_peak > self._peak_linear:
                self._peak_linear = window_peak
            elif wired:
                self._peak_linear *= PEAK_DECAY
            else:
                self._peak_linear = 0.0
            self._online = wired
            self._source = "jack" if wired else "none"

    def _reset_display(self, *, online: bool) -> None:
        self._wired = False
        with self._lock:
            self._online = online
            self._peak_linear = 0.0
            self._period_peak = 0.0
            self._source = "none"

    def _ensure_jack_client(self) -> bool:
        if self._client is not None:
            return True
        if self._jack_available is False:
            return False
        try:
            import jack
        except ImportError:
            self._jack_available = False
            return False

        self._jack = jack
        try:
            client = jack.Client(METER_JACK_CLIENT, no_start_server=True)
            inports = [
                client.inports.register("in_1"),
                client.inports.register("in_2"),
            ]
            client.set_process_callback(self._process)
            client.activate()
        except Exception:
            self._jack_available = False
            self._shutdown_jack()
            return False

        self._client = client
        self._inports = inports
        self._jack_available = True
        return True

    def _process(self, frames: int) -> None:
        peak = 0.0
        for port in self._inports:
            try:
                arr = port.get_array()
            except Exception:
                try:
                    buf = port.get_buffer()
                except Exception:
                    continue
                peak = max(peak, _buffer_peak(buf, frames))
                continue
            if arr is None or len(arr) == 0:
                continue
            count = min(frames, len(arr))
            for idx in range(count):
                sample = float(arr[idx])
                if sample != sample:
                    continue
                a = -sample if sample < 0.0 else sample
                if a > peak:
                    peak = a
        if peak > self._period_peak:
            self._period_peak = peak

    def _try_connect_surge_outputs(self) -> None:
        client = self._client
        if client is None:
            self._wired = False
            return
        for ch, port in enumerate(self._inports, start=1):
            src = f"{self.surge_client}:out_{ch}"
            try:
                if port.is_connected_to(src):
                    continue
                client.connect(src, port)
            except Exception as exc:
                msg = str(exc).lower()
                if "already connected" in msg or "already exists" in msg:
                    continue
                self._wired = False
                return
        self._wired = self._surge_outputs_connected()

    def _surge_outputs_connected(self) -> bool:
        if not self._inports or len(self._inports) < 2:
            return False
        try:
            return all(
                port.is_connected_to(f"{self.surge_client}:out_{ch}")
                for ch, port in enumerate(self._inports, start=1)
            )
        except Exception:
            return self._wired

    def _shutdown_jack(self) -> None:
        client = self._client
        self._client = None
        self._inports = []
        if client is None:
            return
        try:
            client.deactivate()
            client.close()
        except Exception:
            pass
