"""Pure-logic stereo loop buffer and mix helpers (no ALSA I/O)."""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass

try:
    import audioop

    _AUDIOOP_BACKEND = "stdlib"
except ImportError:  # pragma: no cover — removed in Python 3.13+
    try:
        import audioop_lts as audioop  # type: ignore[no-redef]

        _AUDIOOP_BACKEND = "lts"
    except ImportError:
        audioop = None  # type: ignore[assignment]
        _AUDIOOP_BACKEND = "python"

if sys.version_info >= (3, 13) and audioop is not None:
    _AUDIOOP_BACKEND = "lts"


def audio_mix_backend() -> str:
    """Which mixer implementation ``mix_live_and_loops`` uses (for deploy diagnostics)."""
    return _AUDIOOP_BACKEND

S16_STEREO_FRAME_BYTES = 4
S16_CLIP = 32767
_AUDIOOP_WIDTH = 2
_GAIN_SCALE = 32768


def frames_to_bytes(frames: int) -> int:
    return frames * S16_STEREO_FRAME_BYTES


def bytes_to_frames(byte_count: int) -> int:
    return byte_count // S16_STEREO_FRAME_BYTES


def loop_length_frames(*, bars: int, bpm: float, sample_rate: int, beats_per_bar: int = 4) -> int:
    """Sample-accurate length of ``bars`` at ``bpm`` (quarter-note grid)."""
    if bars <= 0:
        raise ValueError("bars must be positive")
    if bpm <= 0:
        raise ValueError("bpm must be positive")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    seconds = bars * beats_per_bar * 60.0 / bpm
    return max(1, int(round(seconds * sample_rate)))


def quantize_loop_frames(
    filled_frames: int,
    *,
    frames_per_bar: int,
    capacity_frames: int,
) -> int:
    """Round recorded length up to the next bar (minimum one bar, capped at capacity)."""
    if frames_per_bar <= 0:
        raise ValueError("frames_per_bar must be positive")
    if capacity_frames <= 0:
        raise ValueError("capacity_frames must be positive")
    if filled_frames <= 0:
        return min(frames_per_bar, capacity_frames)
    bars = (filled_frames + frames_per_bar - 1) // frames_per_bar
    return min(max(bars * frames_per_bar, frames_per_bar), capacity_frames)


@dataclass
class StereoRingBuffer:
    """Fixed-capacity stereo S16_LE ring (interleaved L,R)."""

    capacity_frames: int

    def __post_init__(self) -> None:
        if self.capacity_frames <= 0:
            raise ValueError("capacity_frames must be positive")
        self._data = bytearray(frames_to_bytes(self.capacity_frames))
        self._write_frame = 0
        self._filled_frames = 0

    @property
    def filled_frames(self) -> int:
        return min(self._filled_frames, self.capacity_frames)

    @property
    def is_full(self) -> bool:
        return self._filled_frames >= self.capacity_frames

    def clear(self) -> None:
        self._write_frame = 0
        self._filled_frames = 0

    def write_frames(self, pcm: bytes) -> int:
        """Append up to capacity; returns frames actually stored."""
        if len(pcm) % S16_STEREO_FRAME_BYTES:
            raise ValueError("pcm length must be a whole number of stereo frames")
        incoming = bytes_to_frames(len(pcm))
        if incoming == 0:
            return 0
        stored = 0
        offset = 0
        while stored < incoming and self._filled_frames < self.capacity_frames:
            chunk = min(incoming - stored, self.capacity_frames - self._write_frame)
            byte_off = frames_to_bytes(self._write_frame)
            byte_len = frames_to_bytes(chunk)
            self._data[byte_off : byte_off + byte_len] = pcm[offset : offset + byte_len]
            self._write_frame = (self._write_frame + chunk) % self.capacity_frames
            self._filled_frames += chunk
            stored += chunk
            offset += byte_len
        return stored

    def read_frames(self, start_frame: int, count: int) -> bytes:
        """Read ``count`` frames beginning at ``start_frame`` (mod capacity)."""
        if count <= 0:
            return b""
        if self.filled_frames == 0:
            return b"\x00" * frames_to_bytes(count)

        out = bytearray(frames_to_bytes(count))
        if self.is_full:
            copied = 0
            pos = start_frame % self.capacity_frames
            while copied < count:
                chunk = min(count - copied, self.capacity_frames - pos)
                src_off = frames_to_bytes(pos)
                dst_off = frames_to_bytes(copied)
                byte_len = frames_to_bytes(chunk)
                out[dst_off : dst_off + byte_len] = self._data[src_off : src_off + byte_len]
                pos = (pos + chunk) % self.capacity_frames
                copied += chunk
            return bytes(out)

        effective = min(count, max(0, self.filled_frames - start_frame))
        copied = 0
        pos = start_frame % self.capacity_frames
        while copied < effective:
            chunk = min(effective - copied, self.capacity_frames - pos)
            src_off = frames_to_bytes(pos)
            dst_off = frames_to_bytes(copied)
            byte_len = frames_to_bytes(chunk)
            out[dst_off : dst_off + byte_len] = self._data[src_off : src_off + byte_len]
            pos = (pos + chunk) % self.capacity_frames
            copied += chunk
        return bytes(out)

    def read_frames_for_loop(self, start_frame: int, count: int, loop_frames: int) -> bytes:
        """Read ``count`` frames wrapping at ``loop_frames`` (bar-quantized clip length)."""
        if count <= 0:
            return b""
        if loop_frames <= 0:
            loop_frames = self.capacity_frames
        if self.filled_frames == 0:
            return b"\x00" * frames_to_bytes(count)

        if self.is_full and loop_frames == self.capacity_frames:
            return self.read_frames(start_frame % loop_frames, count)

        filled = self.filled_frames
        out = bytearray(frames_to_bytes(count))
        pos = start_frame % loop_frames
        copied = 0
        while copied < count:
            seg = min(count - copied, loop_frames - pos)
            if pos < filled:
                audio_end = min(pos + seg, filled)
                audio_frames = audio_end - pos
                if audio_frames > 0:
                    src_off = frames_to_bytes(pos)
                    dst_off = frames_to_bytes(copied)
                    byte_len = frames_to_bytes(audio_frames)
                    out[dst_off : dst_off + byte_len] = self._data[src_off : src_off + byte_len]
            copied += seg
            pos = (pos + seg) % loop_frames
        return bytes(out)


def mix_s16_stereo(
    *streams: bytes,
    gains: tuple[float, ...] | None = None,
) -> bytes:
    """Sum aligned stereo S16 streams with per-stream gain; clip to int16 range."""
    if not streams:
        return b""
    frame_count = bytes_to_frames(len(streams[0]))
    for stream in streams[1:]:
        if bytes_to_frames(len(stream)) != frame_count:
            raise ValueError("all streams must have the same frame count")
    if gains is None:
        gains = tuple(1.0 for _ in streams)
    if len(gains) != len(streams):
        raise ValueError("gains length must match streams")

    out = bytearray(len(streams[0]))
    for frame_idx in range(frame_count):
        left = 0.0
        right = 0.0
        base = frame_idx * S16_STEREO_FRAME_BYTES
        for stream, gain in zip(streams, gains, strict=True):
            l_val, r_val = struct.unpack_from("<hh", stream, base)
            left += l_val * gain
            right += r_val * gain
        struct.pack_into(
            "<hh",
            out,
            base,
            int(max(-S16_CLIP - 1, min(S16_CLIP, round(left)))),
            int(max(-S16_CLIP - 1, min(S16_CLIP, round(right)))),
        )
    return bytes(out)


def apply_gain_s16_stereo(pcm: bytes, gain: float) -> bytes:
    if gain == 1.0:
        return pcm
    if audioop is not None:
        factor = max(0, min(_GAIN_SCALE, int(round(gain * _GAIN_SCALE))))
        return audioop.mul(pcm, _AUDIOOP_WIDTH, factor)
    return mix_s16_stereo(pcm, gains=(gain,))


def mix_live_and_loops(
    live_pcm: bytes,
    loop_chunks: list[bytes],
    *,
    live_gain: float = 1.0,
    loop_gain: float = 1.0,
) -> bytes:
    """Sum live input plus zero or more loop layers (fast ``audioop`` path on Pi).

    ``loop_gain`` is the loop bus ceiling: each playing layer uses ``loop_gain / N`` so
    N layers peaking together stay near ``loop_gain``. When ``live_gain > 0`` and
    ``live_gain + loop_gain > 1``, the final mix is scaled down once for headroom.
    """
    if not loop_chunks:
        return apply_gain_s16_stereo(live_pcm, live_gain) if live_gain != 1.0 else live_pcm

    n = len(loop_chunks)
    headroom = (
        1.0 / (live_gain + loop_gain)
        if live_gain > 0.0 and live_gain + loop_gain > 1.0
        else 1.0
    )
    effective_live = live_gain * headroom
    effective_loop_bus = loop_gain * headroom
    per_layer_gain = effective_loop_bus / n

    # Fast audioop path only on CPython 3.12 stdlib; 3.13+ backport (CI) clips before headroom.
    if audioop is not None and _AUDIOOP_BACKEND == "stdlib":
        loop_factor = max(0, min(_GAIN_SCALE, int(round(per_layer_gain * _GAIN_SCALE))))
        loop_sum = loop_chunks[0]
        if loop_factor != _GAIN_SCALE:
            loop_sum = audioop.mul(loop_sum, _AUDIOOP_WIDTH, loop_factor)
        for chunk in loop_chunks[1:]:
            scaled = (
                chunk
                if loop_factor == _GAIN_SCALE
                else audioop.mul(chunk, _AUDIOOP_WIDTH, loop_factor)
            )
            loop_sum = audioop.add(loop_sum, scaled, _AUDIOOP_WIDTH)
        if effective_live != 1.0:
            live_pcm = audioop.mul(
                live_pcm,
                _AUDIOOP_WIDTH,
                int(round(effective_live * _GAIN_SCALE)),
            )
        elif effective_live == 0.0:
            live_pcm = b"\x00" * len(live_pcm)
        return audioop.add(live_pcm, loop_sum, _AUDIOOP_WIDTH)

    frame_count = bytes_to_frames(len(live_pcm))
    out = bytearray(len(live_pcm))
    streams = [live_pcm, *loop_chunks]
    gains_list = [effective_live, *([per_layer_gain] * n)]
    for frame_idx in range(frame_count):
        base = frame_idx * S16_STEREO_FRAME_BYTES
        left = 0.0
        right = 0.0
        for stream, gain in zip(streams, gains_list, strict=True):
            l_val, r_val = struct.unpack_from("<hh", stream, base)
            left += l_val * gain
            right += r_val * gain
        struct.pack_into(
            "<hh",
            out,
            base,
            int(max(-S16_CLIP - 1, min(S16_CLIP, round(left)))),
            int(max(-S16_CLIP - 1, min(S16_CLIP, round(right)))),
        )
    return bytes(out)
