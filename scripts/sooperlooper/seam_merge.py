"""Merge a parallel tail recording onto the wrap seam of a main loop buffer.

Tier 3 path (looper-loop-seam-spec.md): the tail is captured on a scratch loop
while the main loop plays at fixed length N. The tail is the audio that kept
sounding *after* sample N — the release of notes the take cut off.

Model (2026-08-25): the tail is **summed into the head**, wrapping modulo N.
It is not welded onto [N-L, N) and the head is never crossfaded with the end.
That is what a hardware looper does and it is the only model where the take is
preserved bit-for-bit: on the second pass you hear the ring-out of pass one
underneath the attack of pass two, which is exactly what the player heard live.

Why the previous model popped: it crossfaded main->tail across [N-L, N)
(destroying the last L samples of the actual take) and then crossfaded the head
[0, M) toward the seam. At i = M the head snapped back to untouched main in one
sample — a full-scale step every wrap. With main=1.0, tail=0.3, M=256 the merged
buffer stepped 0.30 -> 1.00 between samples 255 and 256.

Level rule: fades applied to the tail are **linear** (a single signal going to
and from silence). Equal-power is for crossfading two decorrelated signals; used
on a fade to silence it lifts the middle by ~3 dB, which is the "tail gets loud
at that part" symptom.

Pure functions only — no OSC, no MIDI. Tests use float32 WAV fixtures.

SooperLooper save_loop writes IEEE float32 WAV (wFormatTag=3). Python's
stdlib wave module rejects that format on read (Pi 3.13: "unknown format: 3"),
so RIFF parsing is manual here.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Sequence

# Fade-out at the tail's truncation point: ~5 ms at 48 kHz. The tail is cut
# wherever capture stopped, so it needs a real ramp to silence.
DEFAULT_DECLICK_SAMPLES = 256

# Fade-IN at the wrap: 64 samples, ~1.3 ms. Deliberately far shorter than the
# fade-out, because the two edges are not the same problem.
#
# The tail's first sample lands at loop index 0, where it is *continuing* the
# energy of the take's end — not starting from silence. Ramping it up from zero
# digs a hole exactly at the seam. Measured on the 00:50 take (3.968 s clip),
# 1 ms RMS windows across the wrap, against a take-end level of 0.193:
#
#   fade_in=256   0.110  0.049  0.086  0.103  0.167 ...   dip to 0.25x  <- stutter
#   fade_in=64    0.110  0.155  0.183  0.155  0.195 ...   dip to 0.52x
#
# Sweep of the worst 1 ms window in the first 10 ms: 0 -> 0.46x, 32 -> 0.52x,
# 64 -> 0.52x, 128 -> 0.48x, 256 -> 0.25x. 64 is the flat part of the curve;
# it still kills any step (0.0012 full-scale at the wrap) without the hole.
DEFAULT_FADE_IN_SAMPLES = 64

_WAVE_FORMAT_IEEE_FLOAT = 3


def _declicked(
    tail: Sequence[tuple[float, float]], fade_in: int, fade_out: int
) -> list[tuple[float, float]]:
    """Linear fades on the tail edges. The two edges are asymmetric on purpose.

    The trailing edge is a truncation and needs a real ramp to silence. The
    leading edge lands on the wrap, where the tail continues the take's energy,
    so it gets only enough ramp to kill the step — see DEFAULT_FADE_IN_SAMPLES.
    """
    frames = list(tail)
    n = len(frames)
    if n == 0:
        return frames
    fi = max(0, min(int(fade_in), n // 2))
    fo = max(0, min(int(fade_out), n // 2))
    for i in range(fi):
        g = (i + 1) / (fi + 1)
        l, r = frames[i]
        frames[i] = (l * g, r * g)
    for i in range(fo):
        g = (i + 1) / (fo + 1)
        j = n - 1 - i
        l, r = frames[j]
        frames[j] = (l * g, r * g)
    return frames


def merge_stereo_frames(
    main: Sequence[tuple[float, float]],
    tail: Sequence[tuple[float, float]],
    *,
    merge_samples: int = 0,
    declick_samples: int = DEFAULT_DECLICK_SAMPLES,
    fade_in_samples: int = DEFAULT_FADE_IN_SAMPLES,
    offset_samples: int = 0,
) -> list[tuple[float, float]]:
    """Sum the release tail into the loop head, wrapping modulo N.

    ``main`` is returned unmodified except for the samples the tail lands on.
    ``offset_samples`` shifts where the tail starts, to compensate for the delay
    between the stop instant and the scratch loop actually arming (the scratch
    record only fires once SL reports the main loop PLAYING).  ``merge_samples``
    is accepted and ignored — it named the head/end crossfade that caused the
    seam pop and no longer exists.
    """
    n = len(main)
    if n == 0:
        return []
    out = list(main)
    if not tail:
        return out

    # Cap at one loop length. The tail is a single acoustic event; wrapping it
    # more than once bakes N overlapping copies of the same ring-out into a
    # buffer that then repeats forever — level buildup and mud, not realism.
    # Reachable now that the tail ends on actual decay (up to
    # TAIL_ABSOLUTE_MAX_S) rather than the old fixed 750 ms cut.
    # declick_samples=0 means "no fades at all" — keep that contract.
    fade_in = fade_in_samples if declick_samples else 0
    frames = _declicked(list(tail)[:n], fade_in, declick_samples)
    start = int(offset_samples) % n
    for i, (tl, tr) in enumerate(frames):
        idx = (start + i) % n
        l, r = out[idx]
        out[idx] = (l + tl, r + tr)
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
    merge_samples: int = 0,
    declick_samples: int = DEFAULT_DECLICK_SAMPLES,
    fade_in_samples: int = DEFAULT_FADE_IN_SAMPLES,
    offset_samples: int = 0,
    offset_seconds: float = 0.0,
) -> Path:
    """Load two SL WAVs, sum the tail into the head, write merged WAV.

    ``offset_seconds`` is where the scratch loop actually started recording,
    as a loop position. The scratch only arms once SL reports the main loop
    PLAYING, so tail[0] is not loop-position 0 — measured at 0.044 s on a
    6.5 s clip. Summing it at index 0 places the ring-out ~44 ms early, i.e.
    less decayed and landing on the take's own attack: a level swell exactly
    at the wrap. ``offset_samples`` is added on top as a manual trim.
    """
    main_frames, main_rate = read_float32_stereo_wav(main_path)
    tail_frames, tail_rate = read_float32_stereo_wav(tail_path)
    if tail_rate != main_rate:
        raise ValueError(
            f"sample rate mismatch: main={main_rate} tail={tail_rate}"
        )
    offset = int(offset_samples) + int(round(offset_seconds * main_rate))
    merged = merge_stereo_frames(
        main_frames,
        tail_frames,
        merge_samples=merge_samples,
        declick_samples=declick_samples,
        fade_in_samples=fade_in_samples,
        offset_samples=offset,
    )
    write_float32_stereo_wav(out_path, merged, sample_rate=main_rate)
    return out_path
