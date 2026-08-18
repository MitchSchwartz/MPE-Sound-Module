"""Merge a parallel tail recording onto the wrap seam of a main loop buffer.

Tier 3 path (looper-loop-seam-spec.md): tail is captured on a scratch loop
while the main loop plays at fixed length N; this module welds tail samples
onto [N-L, N) and crossfades [0, M) with [N-M, N) for wrap continuity.

Pure functions only — no OSC, no MIDI. Tests use float32 WAV fixtures.

SooperLooper save_loop writes IEEE float32 WAV (wFormatTag=3). Python's
stdlib wave module rejects that format on read (Pi 3.13: "unknown format: 3"),
so RIFF parsing is manual here.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Sequence

_WAVE_FORMAT_IEEE_FLOAT = 3


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


def _iter_wav_chunks(data: bytes):
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF WAVE file")
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(data):
            raise ValueError("truncated WAV chunk")
        yield chunk_id, data[chunk_start:chunk_end]
        offset = chunk_end + (chunk_size & 1)


def read_float32_stereo_wav(path: Path) -> tuple[list[tuple[float, float]], int]:
    """Read IEEE float stereo WAV; returns (frames, sample_rate)."""
    data = path.read_bytes()
    fmt: tuple[int, int, int] | None = None
    pcm: bytes | None = None
    for chunk_id, chunk in _iter_wav_chunks(data):
        if chunk_id == b"fmt ":
            if len(chunk) < 16:
                raise ValueError(f"short fmt chunk: {path}")
            (
                audio_format,
                channels,
                sample_rate,
                _byte_rate,
                _block_align,
                bits_per_sample,
            ) = struct.unpack_from("<HHIIHH", chunk, 0)
            if audio_format != _WAVE_FORMAT_IEEE_FLOAT:
                raise ValueError(
                    f"expected IEEE float WAV (format 3), got {audio_format}: {path}"
                )
            if channels != 2 or bits_per_sample != 32:
                raise ValueError(f"expected 32-bit stereo float WAV: {path}")
            fmt = (channels, sample_rate, bits_per_sample)
        elif chunk_id == b"data":
            pcm = chunk
    if fmt is None or pcm is None:
        raise ValueError(f"missing fmt or data chunk: {path}")
    channels, sample_rate, bits_per_sample = fmt
    frame_bytes = channels * (bits_per_sample // 8)
    count = len(pcm) // frame_bytes
    frames: list[tuple[float, float]] = []
    for i in range(count):
        l, r = struct.unpack_from("<ff", pcm, i * frame_bytes)
        frames.append((float(l), float(r)))
    return frames, sample_rate


def write_float32_stereo_wav(
    path: Path,
    frames: Sequence[tuple[float, float]],
    *,
    sample_rate: int,
) -> None:
    """Write IEEE float stereo WAV (SooperLooper save_loop format)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = bytearray()
    for left, right in frames:
        pcm.extend(struct.pack("<ff", float(left), float(right)))
    channels = 2
    bits = 32
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    fmt_chunk = struct.pack(
        "<HHIIHH",
        _WAVE_FORMAT_IEEE_FLOAT,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
    )
    data_chunk = bytes(pcm)
    riff_size = 4 + (8 + len(fmt_chunk)) + (8 + len(data_chunk))
    out = bytearray()
    out.extend(b"RIFF")
    out.extend(struct.pack("<I", riff_size))
    out.extend(b"WAVE")
    out.extend(b"fmt ")
    out.extend(struct.pack("<I", len(fmt_chunk)))
    out.extend(fmt_chunk)
    out.extend(b"data")
    out.extend(struct.pack("<I", len(data_chunk)))
    out.extend(data_chunk)
    path.write_bytes(bytes(out))


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
