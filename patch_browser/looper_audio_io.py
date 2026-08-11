"""ALSA arecord/aplay helpers for the on-device looper."""

from __future__ import annotations

import subprocess
import sys


def open_arecord(device: str, *, sample_rate: int, period_frames: int) -> subprocess.Popen[bytes]:
    buffer_frames = max(period_frames * 2, period_frames + 1)
    return subprocess.Popen(
        [
            "arecord",
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-c",
            "2",
            "-r",
            str(sample_rate),
            "--buffer-size",
            str(buffer_frames),
            "--period-size",
            str(period_frames),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def open_aplay(device: str, *, sample_rate: int, period_frames: int) -> subprocess.Popen[bytes]:
    buffer_frames = max(period_frames * 2, period_frames + 1)
    return subprocess.Popen(
        [
            "aplay",
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-c",
            "2",
            "-r",
            str(sample_rate),
            "--buffer-size",
            str(buffer_frames),
            "--period-size",
            str(period_frames),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def ensure_audio_procs_started(*procs: tuple[str, subprocess.Popen[object]]) -> int | None:
    for label, proc in procs:
        if proc.poll() is None:
            continue
        err = proc.stderr.read() if proc.stderr is not None else b""
        text = err.decode("utf-8", errors="replace").strip()
        print(f"Error: {label} failed to start: {text or 'unknown'}", file=sys.stderr)
        return 2
    return None
