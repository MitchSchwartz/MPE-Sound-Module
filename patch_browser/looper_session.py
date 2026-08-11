"""Interactive single-loop session state (pure logic — no ALSA/MIDI)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from patch_browser.control_surfaces.types import LooperTransportAction
from patch_browser.looper_engine import (
    StereoRingBuffer,
    apply_gain_s16_stereo,
    mix_s16_stereo,
)


class LooperMode(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PLAYING = "playing"
    STOPPED = "stopped"


@dataclass
class LooperSession:
    ring: StereoRingBuffer
    loop_gain: float = 0.85
    mode: LooperMode = LooperMode.IDLE
    playback_frame: int = 0

    @property
    def has_loop(self) -> bool:
        return self.ring.filled_frames > 0 and self.mode != LooperMode.RECORDING

    def clear_loop(self) -> None:
        self.ring.clear()
        self.playback_frame = 0
        self.mode = LooperMode.IDLE

    def on_transport(self, action: LooperTransportAction) -> None:
        if action == LooperTransportAction.RECORD:
            self._on_record()
        elif action == LooperTransportAction.PLAY_STOP:
            self._on_play_stop()
        elif action == LooperTransportAction.CLEAR:
            self.clear_loop()
        # OVERDUB ignored in v0

    def _on_record(self) -> None:
        if self.mode == LooperMode.RECORDING:
            if self.ring.filled_frames > 0:
                self.mode = LooperMode.PLAYING
                self.playback_frame = 0
            return
        self.ring.clear()
        self.playback_frame = 0
        self.mode = LooperMode.RECORDING

    def _on_play_stop(self) -> None:
        if not self.has_loop and self.mode != LooperMode.RECORDING:
            return
        if self.mode == LooperMode.RECORDING:
            self.mode = LooperMode.PLAYING if self.ring.filled_frames else LooperMode.IDLE
            self.playback_frame = 0
            return
        if self.mode == LooperMode.PLAYING:
            self.mode = LooperMode.STOPPED
        elif self.mode == LooperMode.STOPPED:
            self.mode = LooperMode.PLAYING

    def process_period(self, live_pcm: bytes) -> None:
        """Advance recording if active; auto-switch to PLAYING when ring is full."""
        if self.mode == LooperMode.RECORDING:
            self.ring.write_frames(live_pcm)
            if self.ring.is_full:
                self.mode = LooperMode.PLAYING
                self.playback_frame = 0

    def output_pcm(self, live_pcm: bytes, *, period_frames: int) -> bytes:
        if self.mode == LooperMode.PLAYING and self.ring.filled_frames > 0:
            loop_pcm = self.ring.read_frames(self.playback_frame, period_frames)
            loop_pcm = apply_gain_s16_stereo(loop_pcm, self.loop_gain)
            out = mix_s16_stereo(live_pcm, loop_pcm, gains=(1.0, 1.0))
            cap = self.ring.capacity_frames
            self.playback_frame = (self.playback_frame + period_frames) % cap
            return out
        return live_pcm
