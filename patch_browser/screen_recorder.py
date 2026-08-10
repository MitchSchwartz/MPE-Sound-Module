"""Pipe pygame framebuffer RGB24 frames to an external ffmpeg process (demo capture)."""

from __future__ import annotations

import errno
import os
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygame


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


class ScreenRecorder:
    """Write throttled RGB24 frames to a named pipe for ffmpeg rawvideo ingest."""

    def __init__(
        self,
        *,
        enabled: bool,
        pipe_path: str,
        fps: int,
        width: int,
        height: int,
    ) -> None:
        self._enabled = enabled
        self._pipe_path = pipe_path
        self._fps = fps
        self._width = width
        self._height = height
        self._frame_interval = 1.0 / fps
        self._last_frame_at = 0.0
        self._fd: int | None = None
        self._disabled_reason: str | None = None

    @classmethod
    def from_env(cls) -> ScreenRecorder | None:
        if not _env_flag("MPE_SCREEN_RECORD"):
            return None
        pipe_path = os.environ.get("MPE_SCREEN_RECORD_PIPE", "/tmp/mpe-screen-record.pipe").strip()
        if not pipe_path:
            print("MPE_SCREEN_RECORD=1 but MPE_SCREEN_RECORD_PIPE is empty — recording disabled", file=sys.stderr)
            return None
        return cls(
            enabled=True,
            pipe_path=pipe_path,
            fps=_env_int("MPE_SCREEN_RECORD_FPS", 30),
            width=_env_int("MPE_SCREEN_RECORD_WIDTH", 800),
            height=_env_int("MPE_SCREEN_RECORD_HEIGHT", 480),
        )

    @property
    def active(self) -> bool:
        return self._enabled and self._disabled_reason is None

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def write_frame(self, surface: pygame.Surface, *, now: float | None = None) -> None:
        if not self.active:
            return
        ts = time.monotonic() if now is None else now
        if ts - self._last_frame_at < self._frame_interval:
            return
        if not self._ensure_open():
            return
        size = surface.get_size()
        if size != (self._width, self._height):
            self._disable(
                f"frame size {size[0]}x{size[1]} != "
                f"MPE_SCREEN_RECORD_WIDTH/HEIGHT {self._width}x{self._height}"
            )
            return
        import pygame

        try:
            payload = pygame.image.tostring(surface, "RGB")
            os.write(self._fd, payload)
            self._last_frame_at = ts
        except BrokenPipeError:
            self._disable("ffmpeg reader closed the pipe")
        except OSError as exc:
            self._disable(f"pipe write failed: {exc}")

    def _ensure_open(self) -> bool:
        if self._fd is not None:
            return True
        try:
            self._fd = os.open(self._pipe_path, os.O_WRONLY | os.O_NONBLOCK)
            print(
                f"Screen record → {self._pipe_path} @ {self._fps}fps "
                f"({self._width}x{self._height} rgb24)",
                file=sys.stderr,
            )
            return True
        except OSError as exc:
            if exc.errno in (errno.ENXIO, errno.ENOENT):
                return False
            self._disable(f"cannot open {self._pipe_path}: {exc}")
            return False

    def _disable(self, reason: str) -> None:
        if self._disabled_reason is None:
            print(f"Screen record stopped: {reason}", file=sys.stderr)
        self._disabled_reason = reason
        self.close()
        self._enabled = False
