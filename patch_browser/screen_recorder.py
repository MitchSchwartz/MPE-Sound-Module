"""Pipe pygame framebuffer RGB24 frames to an external ffmpeg process (demo capture)."""

from __future__ import annotations

import errno
import os
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygame

DEFAULT_ENV_FILE = "/tmp/mpe-screen-record.env"
DEFAULT_PIPE = "/tmp/mpe-screen-record.pipe"
_FRAME_QUEUE_MAX = 2
_STOP = object()


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def _env_int(name: str, default: int, overrides: dict[str, str] | None = None) -> int:
    raw = ""
    if overrides and name in overrides:
        raw = overrides[name]
    if not raw:
        raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _parse_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


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
        self._frame_queue: queue.Queue[bytes | object] = queue.Queue(maxsize=_FRAME_QUEUE_MAX)
        self._writer_thread = threading.Thread(target=self._writer_loop, name="mpe-screen-record", daemon=True)
        self._writer_started = False
        self._writer_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> ScreenRecorder | None:
        if not _env_flag("MPE_SCREEN_RECORD"):
            return None
        pipe_path = os.environ.get("MPE_SCREEN_RECORD_PIPE", DEFAULT_PIPE).strip()
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

    @classmethod
    def from_env_file(cls, path: str) -> ScreenRecorder | None:
        if not path or not os.path.isfile(path):
            return None
        values = _parse_env_file(path)
        if values.get("MPE_SCREEN_RECORD") != "1":
            return None
        pipe_path = values.get("MPE_SCREEN_RECORD_PIPE", DEFAULT_PIPE).strip()
        if not pipe_path:
            return None
        return cls(
            enabled=True,
            pipe_path=pipe_path,
            fps=_env_int("MPE_SCREEN_RECORD_FPS", 30, values),
            width=_env_int("MPE_SCREEN_RECORD_WIDTH", 800, values),
            height=_env_int("MPE_SCREEN_RECORD_HEIGHT", 480, values),
        )

    @property
    def active(self) -> bool:
        return self._enabled and self._disabled_reason is None

    def close(self) -> None:
        self._enabled = False
        if self._writer_started:
            try:
                self._frame_queue.put_nowait(_STOP)
            except queue.Full:
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._frame_queue.put_nowait(_STOP)
                except queue.Full:
                    pass
            self._writer_thread.join(timeout=3.0)
            self._writer_started = False
        with self._writer_lock:
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
        except pygame.error as exc:
            self._disable(f"frame capture failed: {exc}")
            return
        self._ensure_writer_started()
        try:
            self._frame_queue.put_nowait(payload)
            self._last_frame_at = ts
        except queue.Full:
            pass

    def _ensure_writer_started(self) -> None:
        if self._writer_started:
            return
        self._writer_thread.start()
        self._writer_started = True

    def _writer_loop(self) -> None:
        while True:
            item = self._frame_queue.get()
            if item is _STOP:
                break
            if not isinstance(item, (bytes, bytearray)):
                continue
            if not self._ensure_open():
                continue
            try:
                self._write_all(bytes(item))
            except BrokenPipeError:
                self._disable("ffmpeg reader closed the pipe")
                break
            except OSError as exc:
                self._disable(f"pipe write failed: {exc}")
                break

    def _write_all(self, payload: bytes) -> None:
        if self._fd is None:
            return
        view = memoryview(payload)
        while view:
            try:
                wrote = os.write(self._fd, view)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    time.sleep(0.002)
                    continue
                raise
            if wrote <= 0:
                raise OSError("pipe write returned 0")
            view = view[wrote:]

    def _ensure_open(self) -> bool:
        with self._writer_lock:
            if self._fd is not None:
                return True
            try:
                self._fd = os.open(self._pipe_path, os.O_WRONLY)
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
        self._enabled = False
        with self._writer_lock:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
