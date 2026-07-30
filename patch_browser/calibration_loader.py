#!/usr/bin/env python3
"""
Fullscreen pygame progress UI for patch normalization calibration.

Runs on the Pi DSI display (kmsdrm) while scripts/calibrate-patch-normalization.py
measures loudness. Intended to be launched via scripts/calibrate-with-loader.sh.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pygame
except ImportError as exc:
    print("FATAL: pygame is required for the calibration loader.", file=sys.stderr)
    raise SystemExit(1) from exc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.ui_theme import Theme  # noqa: E402

CALIBRATE_SCRIPT = REPO_ROOT / "scripts" / "calibrate-patch-normalization.py"
DONE_HOLD_SECONDS = 2.5


@dataclass
class LoaderState:
    phase: str = "preparing"
    message: str = "Starting calibration…"
    patch_name: str = ""
    index: int = 0
    total: int = 0
    updated: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    exit_code: int | None = None
    finished: bool = False


@dataclass
class ProgressReader:
    proc: subprocess.Popen[str]
    state_queue: queue.SimpleQueue[dict] = field(default_factory=queue.SimpleQueue)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        def _read_stdout() -> None:
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.state_queue.put(event)

        self._thread = threading.Thread(target=_read_stdout, daemon=True)
        self._thread.start()

    def join(self) -> int:
        code = self.proc.wait()
        if self._thread:
            self._thread.join(timeout=2.0)
        return code


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _load_font(size: int) -> pygame.font.Font:
    for name in ("dejavusans", "liberationsans", "notosans", None):
        try:
            return pygame.font.SysFont(name, size)
        except OSError:
            continue
    return pygame.font.Font(None, size)


def _apply_event(state: LoaderState, event: dict) -> None:
    kind = event.get("type")
    if kind == "start":
        state.phase = "calibrating"
        state.total = int(event.get("total", 0))
        state.message = event.get("scope") or "Calibrating patches…"
    elif kind == "setup":
        state.phase = "preparing"
        state.message = event.get("message") or "Preparing Surge…"
    elif kind == "patch":
        state.phase = "calibrating"
        state.index = int(event.get("index", 0))
        state.total = int(event.get("total", state.total))
        state.patch_name = str(event.get("name", ""))
        state.message = "Calibrating patches…"
    elif kind == "patch_done":
        state.index = int(event.get("index", state.index))
        state.total = int(event.get("total", state.total))
        if event.get("ok"):
            state.updated += 1
    elif kind == "done":
        state.phase = "done"
        state.updated = int(event.get("updated", state.updated))
        state.exit_code = int(event.get("exit_code", 0))
        state.message = (
            f"Done — {state.updated} patch(es) calibrated"
            if state.exit_code == 0
            else "Calibration finished with errors"
        )
        state.finished = True
    elif kind == "error":
        state.phase = "error"
        state.error = str(event.get("message", "Unknown error"))
        state.message = state.error
        state.finished = True


class CalibrationLoaderApp:
    def __init__(self, calibrate_args: list[str]) -> None:
        pygame.init()
        pygame.display.set_caption("Patch Calibration")
        windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
        if windowed:
            self.screen = pygame.display.set_mode((800, 480))
        else:
            self.screen = pygame.display.set_mode((800, 480), pygame.FULLSCREEN)
        self.width, self.height = self.screen.get_size()
        pygame.mouse.set_visible(False)

        self.theme = Theme()
        self.font_title = _load_font(32)
        self.font_md = _load_font(22)
        self.font_sm = _load_font(18)

        self.state = LoaderState()
        self._started_at = time.monotonic()
        self._done_at: float | None = None
        self._running = True

        cmd = [sys.executable, "-u", str(CALIBRATE_SCRIPT), "--progress-json", *calibrate_args]
        self.reader = ProgressReader(
            proc=subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(REPO_ROOT),
            )
        )
        self.reader.start()

    def _elapsed(self) -> float:
        return time.monotonic() - self._started_at

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.reader.state_queue.get_nowait()
            except queue.Empty:
                break
            _apply_event(self.state, event)

    def _draw_progress_bar(self, y: int, w: int, h: int) -> None:
        total = max(self.state.total, 1)
        ratio = min(1.0, self.state.index / total) if self.state.index else 0.0
        x = (self.width - w) // 2
        track = pygame.Rect(x, y, w, h)
        pygame.draw.rect(self.screen, self.theme.surface_alt, track, border_radius=8)
        fill_w = int(w * ratio)
        if fill_w > 0:
            fill = pygame.Rect(x, y, fill_w, h)
            pygame.draw.rect(self.screen, self.theme.accent, fill, border_radius=8)

    def _blit_centered(self, surf: pygame.Surface, y: int) -> None:
        x = (self.width - surf.get_width()) // 2
        self.screen.blit(surf, (x, y))

    def _draw(self) -> None:
        self.screen.fill(self.theme.bg)
        self.state.elapsed_s = self._elapsed()

        title = self.font_title.render("Calibrating patches…", True, self.theme.text)
        self._blit_centered(title, 72)

        if self.state.phase == "preparing":
            sub = self.font_md.render(self.state.message, True, self.theme.muted)
            self._blit_centered(sub, 200)
        elif self.state.phase == "error":
            sub = self.font_md.render(self.state.message[:48], True, self.theme.danger)
            self._blit_centered(sub, 200)
        elif self.state.phase == "done":
            sub = self.font_md.render(self.state.message, True, self.theme.ok)
            self._blit_centered(sub, 200)
        else:
            name = self.state.patch_name or "…"
            patch_s = self.font_md.render(name[:36], True, self.theme.text)
            self._blit_centered(patch_s, 168)

            if self.state.total > 0:
                prog = self.font_md.render(
                    f"{self.state.index} / {self.state.total}",
                    True,
                    self.theme.accent,
                )
                self._blit_centered(prog, 210)
                self._draw_progress_bar(252, min(520, self.width - 80), 14)

        elapsed_s = self.font_sm.render(
            f"Elapsed {_format_elapsed(self.state.elapsed_s)}",
            True,
            self.theme.muted,
        )
        self._blit_centered(elapsed_s, 300)

        if self.state.phase == "calibrating":
            warn = self.font_sm.render(
                "Do not touch — Surge is measuring loudness",
                True,
                self.theme.muted,
            )
            self._blit_centered(warn, self.height - 56)

        if self.state.phase in ("done", "error"):
            hint = self.font_sm.render("Restarting patch browser…", True, self.theme.muted)
            self._blit_centered(hint, self.height - 56)

        pygame.display.flip()

    def run(self) -> int:
        clock = pygame.time.Clock()
        while self._running:
            for event in pygame.event.get():
                if event.type in (pygame.QUIT, pygame.KEYDOWN):
                    if event.type == pygame.KEYDOWN and event.key not in (
                        pygame.K_ESCAPE,
                        pygame.K_q,
                    ):
                        continue
                    # Ignore quit during calibration — Surge must stay exclusive.
                    if not self.state.finished:
                        continue
                    self._running = False

            self._drain_events()
            if self.reader.proc.poll() is not None and not self.state.finished:
                code = self.reader.proc.returncode or 0
                if self.state.phase != "error":
                    _apply_event(
                        self.state,
                        {
                            "type": "done",
                            "updated": self.state.updated,
                            "exit_code": code,
                        },
                    )
                self._done_at = time.monotonic()

            if self.state.finished:
                if self._done_at is None:
                    self._done_at = time.monotonic()
                if time.monotonic() - self._done_at >= DONE_HOLD_SECONDS:
                    self._running = False

            self._draw()
            clock.tick(30)

        exit_code = self.state.exit_code
        if exit_code is None:
            exit_code = self.reader.join()
        pygame.quit()
        return exit_code if exit_code is not None else 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not CALIBRATE_SCRIPT.is_file():
        print(f"Missing calibrator: {CALIBRATE_SCRIPT}", file=sys.stderr)
        return 1
    return CalibrationLoaderApp(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
