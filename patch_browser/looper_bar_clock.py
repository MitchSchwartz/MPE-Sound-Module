"""Sample-accurate bar/beat clock for on-device looper transport and HUD."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LooperBarClock:
    """Advance from audio period callbacks; drives quantize stops and header display."""

    sample_rate: int
    bpm: float
    beats_per_bar: int = 4
    bars_per_loop: int = 4
    _total_frames: int = 0
    _last_bar_index: int = 0

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.bpm <= 0:
            raise ValueError("bpm must be positive")
        if self.beats_per_bar <= 0:
            raise ValueError("beats_per_bar must be positive")
        if self.bars_per_loop <= 0:
            raise ValueError("bars_per_loop must be positive")

    @property
    def frames_per_beat(self) -> int:
        return max(1, int(round(self.sample_rate * 60.0 / self.bpm)))

    @property
    def frames_per_bar(self) -> int:
        return self.frames_per_beat * self.beats_per_bar

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def beat_in_bar(self) -> int:
        """1-based beat within the current bar (1 … beats_per_bar)."""
        beat_index = self._total_frames // self.frames_per_beat
        return (beat_index % self.beats_per_bar) + 1

    @property
    def bar_in_loop(self) -> int:
        """1-based bar within the configured loop length (1 … bars_per_loop)."""
        bar_index = self._total_frames // self.frames_per_bar
        return (bar_index % self.bars_per_loop) + 1

    @property
    def bar_index(self) -> int:
        """Absolute bar counter since clock start (0-based)."""
        return self._total_frames // self.frames_per_bar

    def advance(self, period_frames: int) -> bool:
        """Advance by one audio period; return True if a bar boundary was crossed."""
        if period_frames <= 0:
            return False
        before_bar = self.bar_index
        self._total_frames += period_frames
        after_bar = self.bar_index
        crossed = after_bar > before_bar
        if crossed:
            self._last_bar_index = after_bar
        return crossed

    def snapshot(self) -> dict[str, int | float]:
        return {
            "bpm": self.bpm,
            "beats_per_bar": self.beats_per_bar,
            "bars_per_loop": self.bars_per_loop,
            "beat_in_bar": self.beat_in_bar,
            "bar_in_loop": self.bar_in_loop,
            "total_frames": self._total_frames,
        }
