"""Pure-logic stereo loop buffer and mix helpers (no ALSA I/O)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

S16_STEREO_FRAME_BYTES = 4
S16_CLIP = 32767


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
        effective = min(count, self.filled_frames)
        out = bytearray(frames_to_bytes(count))
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
    return mix_s16_stereo(pcm, gains=(gain,))
