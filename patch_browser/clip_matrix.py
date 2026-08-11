"""Multi-clip Session View matrix — pure logic (no ALSA/MIDI)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from patch_browser.looper_bar_clock import LooperBarClock
from patch_browser.looper_engine import (
    StereoRingBuffer,
    loop_length_frames,
    mix_live_and_loops,
    quantize_loop_frames,
)


class ClipState(StrEnum):
    EMPTY = "empty"
    RECORDING = "recording"
    STOPPED = "stopped"
    PLAYING = "playing"
    STOPPING = "stopping"


@dataclass
class ClipSlot:
    row: int
    col: int
    ring: StereoRingBuffer
    state: ClipState = ClipState.EMPTY
    playback_frame: int = 0
    loop_frames: int = 0

    @property
    def has_content(self) -> bool:
        return self.ring.filled_frames > 0

    def clear(self) -> None:
        self.ring.clear()
        self.playback_frame = 0
        self.loop_frames = 0
        self.state = ClipState.EMPTY


@dataclass
class ClipMatrix:
    """Ableton-style clip grid with bar-quantized stops."""

    clock: LooperBarClock
    loop_frames: int
    enabled_slots: frozenset[tuple[int, int]]
    slots: dict[tuple[int, int], ClipSlot] = field(default_factory=dict)
    loop_gain: float = 0.85
    live_gain: float = 1.0

    def slot(self, row: int, col: int) -> ClipSlot | None:
        key = (row, col)
        if key not in self.enabled_slots:
            return None
        if key not in self.slots:
            self.slots[key] = ClipSlot(
                row=row,
                col=col,
                ring=StereoRingBuffer(self.loop_frames),
            )
        return self.slots[key]

    @property
    def is_active(self) -> bool:
        return any(
            s.state in (ClipState.RECORDING, ClipState.PLAYING, ClipState.STOPPING)
            for s in self.slots.values()
        )

    def _begin_playback(self, clip: ClipSlot) -> None:
        clip.state = ClipState.PLAYING
        clip.playback_frame = 0
        cap = clip.ring.capacity_frames
        if clip.ring.is_full:
            clip.loop_frames = cap
        else:
            clip.loop_frames = quantize_loop_frames(
                clip.ring.filled_frames,
                frames_per_bar=self.clock.frames_per_bar,
                capacity_frames=cap,
            )

    def on_grid(self, row: int, col: int) -> None:
        clip = self.slot(row, col)
        if clip is None:
            return
        if clip.state == ClipState.EMPTY:
            clip.clear()
            clip.state = ClipState.RECORDING
            return
        if clip.state == ClipState.RECORDING:
            if clip.has_content:
                self._begin_playback(clip)
            else:
                clip.state = ClipState.EMPTY
                clip.playback_frame = 0
            return
        if clip.state == ClipState.STOPPED and clip.has_content:
            self._begin_playback(clip)
            return
        if clip.state == ClipState.PLAYING:
            clip.state = ClipState.STOPPED
            clip.playback_frame = 0

    def on_scene(self, row: int) -> None:
        row_slots: list[ClipSlot] = []
        for key in self.enabled_slots:
            if key[0] != row:
                continue
            clip = self.slot(key[0], key[1])
            if clip is not None:
                row_slots.append(clip)
        if not row_slots:
            return
        active = [s for s in row_slots if s.state in (ClipState.PLAYING, ClipState.STOPPING)]
        if active:
            for s in row_slots:
                if s.state in (ClipState.PLAYING, ClipState.STOPPING):
                    s.state = ClipState.STOPPING
            return
        for s in row_slots:
            if s.state == ClipState.STOPPED and s.has_content:
                s.state = ClipState.PLAYING
                s.playback_frame = 0

    def on_stop_all(self) -> None:
        for s in self.slots.values():
            if (s.row, s.col) not in self.enabled_slots:
                continue
            if s.state in (ClipState.PLAYING, ClipState.STOPPING):
                s.state = ClipState.STOPPING

    def clear_session(self) -> None:
        """Wipe all enabled clips immediately (empty grid, fresh session)."""
        for key in self.enabled_slots:
            clip = self.slots.get(key)
            if clip is not None:
                clip.clear()
        self.clock.reset()

    def _apply_bar_boundary(self) -> None:
        for s in self.slots.values():
            if s.state == ClipState.STOPPING:
                s.state = ClipState.STOPPED
                s.playback_frame = 0

    def process_period(self, live_pcm: bytes, *, period_frames: int) -> bytes:
        if self.clock.advance(period_frames):
            self._apply_bar_boundary()

        loop_chunks: list[bytes] = []

        for key in self.enabled_slots:
            clip = self.slots.get(key)
            if clip is None:
                continue

            if clip.state == ClipState.RECORDING:
                clip.ring.write_frames(live_pcm)
                if clip.ring.is_full:
                    self._begin_playback(clip)
                continue

            if clip.state not in (ClipState.PLAYING,):
                continue

            loop_chunks.append(
                clip.ring.read_frames_for_loop(
                    clip.playback_frame,
                    period_frames,
                    clip.loop_frames or clip.ring.capacity_frames,
                )
            )
            loop_len = clip.loop_frames or clip.ring.capacity_frames
            clip.playback_frame = (clip.playback_frame + period_frames) % loop_len

        return mix_live_and_loops(
            live_pcm,
            loop_chunks,
            live_gain=self.live_gain,
            loop_gain=self.loop_gain,
        )

    @classmethod
    def create_v1(
        cls,
        *,
        sample_rate: int,
        bpm: float,
        bars: int,
        loop_gain: float = 0.85,
        beats_per_bar: int = 4,
    ) -> ClipMatrix:
        loop_frames = loop_length_frames(bars=bars, bpm=bpm, sample_rate=sample_rate)
        clock = LooperBarClock(
            sample_rate=sample_rate,
            bpm=bpm,
            beats_per_bar=beats_per_bar,
            bars_per_loop=bars,
        )
        return cls(
            clock=clock,
            loop_frames=loop_frames,
            enabled_slots=frozenset((0, col) for col in range(8)),
            loop_gain=loop_gain,
        )
