"""Lightweight ALSA output peak meter for limiter-at-ceiling detection."""

from __future__ import annotations

import math
import os
import struct
import subprocess
import threading

from patch_browser.audio_profile import is_usb_host
from patch_browser.calibration_standalone import resolve_standalone_capture_device

SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2
READ_FRAMES = 2048
PEAK_DECAY = 0.92
MIN_PEAK_DBFS = -80.0


def resolve_output_monitor_device() -> str | None:
    """Return an ALSA capture device that snoops Surge playback, if available."""
    if os.environ.get("MPE_LIMITER_PEAK_METER", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    if is_usb_host():
        # Pi → host UAC2 has no local playback tap without snd-aloop.
        return None
    return resolve_standalone_capture_device()


def linear_to_dbtp(linear: float) -> float:
    if linear <= 0.0:
        return MIN_PEAK_DBFS
    return max(MIN_PEAK_DBFS, 20.0 * math.log10(linear))


class OutputPeakMonitor:
    """Poll Surge output peaks via arecord on a dsnoop/monitor PCM."""

    def __init__(self, device: str | None = None) -> None:
        self._device = device if device is not None else resolve_output_monitor_device()
        self._lock = threading.Lock()
        self._peak_linear = 0.0
        self._online = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._device is None:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="OutputPeakMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot(self) -> dict:
        with self._lock:
            peak_db = linear_to_dbtp(self._peak_linear)
            return {
                "online": self._online,
                "peak_linear": self._peak_linear,
                "peak_dbtp": peak_db,
            }

    def _worker(self) -> None:
        while not self._stop.is_set():
            if not self._capture_loop():
                with self._lock:
                    self._online = False
                    self._peak_linear *= PEAK_DECAY
                self._stop.wait(0.5)

    def _capture_loop(self) -> bool:
        device = self._device
        if not device:
            return False
        chunk_bytes = READ_FRAMES * CHANNELS * SAMPLE_WIDTH
        cmd = [
            "arecord",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-r",
            str(SAMPLE_RATE),
            "-c",
            str(CHANNELS),
            "-t",
            "raw",
            "-q",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False
        self._proc = proc
        ok = False
        try:
            with self._lock:
                self._online = True
            while not self._stop.is_set() and proc.poll() is None:
                assert proc.stdout is not None
                data = proc.stdout.read(chunk_bytes)
                if not data:
                    break
                ok = True
                peak = self._peak_from_pcm(data)
                with self._lock:
                    self._peak_linear = max(peak, self._peak_linear * PEAK_DECAY)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self._proc = None
        return ok

    @staticmethod
    def _peak_from_pcm(data: bytes) -> float:
        count = len(data) // SAMPLE_WIDTH
        if count <= 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", data[: count * SAMPLE_WIDTH])
        peak_raw = max(abs(sample) for sample in samples)
        if peak_raw <= 0:
            return 0.0
        return min(1.0, peak_raw / 32768.0)
