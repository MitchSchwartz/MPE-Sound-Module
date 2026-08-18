"""Merge a parallel tail recording onto the wrap seam of a main loop buffer.

Tier 3 path (looper-loop-seam-spec.md): tail is captured on a scratch loop
while the main loop plays at fixed length N; this module welds tail samples
onto [N-L, N) and crossfades [0, M) with [N-M, N) for wrap continuity.

Pure functions only — no OSC, no MIDI. Tests use float32 WAV fixtures.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Sequence


def _equal_power(a: float, b: float, t: float) -> float:
    """Blend b in as t goes 0→1 (equal-power crossfade)."""
    t = max(0.0, min(1.0, t))
    w0 = math.cos(t * math.pi / 2.0)
    w1 = math.sin(t * math.pi / 2.0)
    return a * w0 + b * w1


def merge_stereo_frames(
    main: Sequence[tuple[float, float]],
    tail: Sequence[tuple[float, float]],
    *,
    merge_samples: int,
) -> list[tuple[float, float]]:
    """Return new main buffer with tail welded at the end and wrap smoothed."""
    n = len(main)
    if n == 0:
        return []
    if not tail:
        return list(main)

    m = max(0, min(int(merge_samples), n // 2))
    use = min(len(tail), n)
    if use <= 0:
        return list(main)

    out = list(main)
    for i in range(use):
        idx = n - use + i
        t = i / max(use - 1, 1)
        ml, mr = main[idx]
        tl, tr = tail[i]
        out[idx] = (_equal_power(ml, tl, t), _equal_power(mr, tr, t))

    if m > 1:
        for i in range(m):
            t = i / max(m - 1, 1)
            head = out[i]
            seam = out[n - m + i]
            out[i] = (_equal_power(head[0], seam[0], t), _equal_power(head[1], seam[1], t))

    return out


def read_float32_stereo_wav(path: Path) -> tuple[list[tuple[float, float]], int]:
    """Read IEEE float stereo WAV; returns (frames, sample_rate)."""
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 4 or wf.getcomptype() != "NONE":
            raise ValueError(f"expected 32-bit float PCM WAV: {path}")
        channels = wf.getnchannels()
        if channels != 2:
            raise ValueError(f"expected stereo WAV: {path}")
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    count = len(raw) // 8
    frames: list[tuple[float, float]] = []
    for i in range(count):
        l, r = struct.unpack_from("<ff", raw, i * 8)
        frames.append((float(l), float(r)))
    return frames, rate


def write_float32_stereo_wav(
    path: Path,
    frames: Sequence[tuple[float, float]],
    *,
    sample_rate: int,
) -> None:
    """Write IEEE float stereo WAV (SooperLooper save_loop format)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(4)
        wf.setframerate(sample_rate)
        wf.setcomptype("NONE", "not compressed")
        buf = bytearray()
        for left, right in frames:
            buf.extend(struct.pack("<ff", float(left), float(right)))
        wf.writeframes(bytes(buf))


def merge_tail_at_seam(
    main_path: Path,
    tail_path: Path,
    out_path: Path,
    *,
    merge_samples: int,
) -> Path:
    """Load two SL WAVs, weld tail at seam, write merged WAV."""
    main_frames, main_rate = read_float32_stereo_wav(main_path)
    tail_frames, tail_rate = read_float32_stereo_wav(tail_path)
    if tail_rate != main_rate:
        raise ValueError(
            f"sample rate mismatch: main={main_rate} tail={tail_rate}"
        )
    merged = merge_stereo_frames(
        main_frames, tail_frames, merge_samples=merge_samples
    )
    write_float32_stereo_wav(out_path, merged, sample_rate=main_rate)
    return out_path
