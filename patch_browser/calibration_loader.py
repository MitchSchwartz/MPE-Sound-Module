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

from patch_browser.calibration_teardown import (  # noqa: E402
    exec_touch_patch_browser,
    restore_mpe_audio_services,
)
from patch_browser.calibration_constants import calibration_from_browser  # noqa: E402
from patch_browser.dsi_splash import (
    CAL_RETURN_HOLD_SECONDS,
    SplashMode,
    draw_splash_frame,
    paint_immediate,
)
from patch_browser.geometry import Rect  # noqa: E402
from patch_browser.ui_text import (
    draw_wrapped_text_in_rect,
    text_block_height,
    wrap_text_lines,
)
from patch_browser.ui_theme import (  # noqa: E402
    reload_theme_from_prefs,
    theme_for_mode,
    theme_semantic_color,
)

CALIBRATE_SCRIPT = REPO_ROOT / "scripts" / "calibrate-patch-normalization.py"
DONE_HOLD_SECONDS = 2.5
CANCEL_BTN_W = 160
CANCEL_BTN_H = 44
LOADER_STDERR_LOG = Path("/tmp/calibration-loader.stderr")
LOADER_FAILURE_REPORT = Path("/tmp/calibration-loader-last-exit.json")


def format_cancel_message(*, saved: int, attempted: int) -> str:
    """Human-readable cancel summary — saved count is successes, not attempt index."""
    if saved:
        if saved == attempted:
            return f"Cancelled — {saved} patch(es) saved"
        return f"Cancelled — {saved} calibration(s) saved before cancel"
    if attempted:
        return (
            "Cancelled — saved 0 calibrations "
            f"({attempted} patch(es) attempted; none measured successfully)"
        )
    return "Cancelled — saved 0 calibrations"


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
    cancelled: bool = False


@dataclass
class ProgressReader:
    proc: subprocess.Popen[str]
    state_queue: queue.SimpleQueue[dict] = field(default_factory=queue.SimpleQueue)
    _thread: threading.Thread | None = None
    _stderr_thread: threading.Thread | None = None

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

        def _drain_stderr() -> None:
            assert self.proc.stderr is not None
            try:
                log_handle = LOADER_STDERR_LOG.open("a", encoding="utf-8")
            except OSError:
                log_handle = None
            try:
                for line in self.proc.stderr:
                    if log_handle is not None:
                        log_handle.write(line)
                        log_handle.flush()
            finally:
                if log_handle is not None:
                    log_handle.close()

        self._thread = threading.Thread(target=_read_stdout, daemon=True)
        self._thread.start()
        if self.proc.stderr is not None:
            self._stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            self._stderr_thread.start()

    def join(self) -> int:
        code = self.proc.wait()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._stderr_thread:
            self._stderr_thread.join(timeout=2.0)
        return code

    def terminate(self) -> None:
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=4.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2.0)


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


def _write_loader_exit_report(state: LoaderState, *, subprocess_code: int | None) -> None:
    payload = {
        "phase": state.phase,
        "patch_name": state.patch_name,
        "patch_index": state.index,
        "total": state.total,
        "updated": state.updated,
        "error": state.error,
        "exit_code": state.exit_code if state.exit_code is not None else subprocess_code,
        "subprocess_code": subprocess_code,
        "cancelled": state.cancelled,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        LOADER_FAILURE_REPORT.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


class CalibrationLoaderApp:
    def __init__(
        self,
        calibrate_args: list[str],
        *,
        preopened_screen: "pygame.Surface | None" = None,
    ) -> None:
        if preopened_screen is not None:
            self.screen = preopened_screen
            self.width, self.height = self.screen.get_size()
        else:
            pygame.init()
            pygame.display.set_caption("Patch Calibration")
            windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
            if windowed:
                self.screen = pygame.display.set_mode((800, 480))
            else:
                self.screen = pygame.display.set_mode((800, 480), pygame.FULLSCREEN)
            self.width, self.height = self.screen.get_size()
        pygame.mouse.set_visible(False)

        prefs = reload_theme_from_prefs()
        self.theme = theme_for_mode(prefs.theme_mode)
        self.font_title = _load_font(32)
        self.font_md = _load_font(22)
        self.font_sm = _load_font(18)
        draw_splash_frame(
            self.screen,
            mode=SplashMode.CAL_ENTER,
            theme=self.theme,
            progress=0.05,
        )
        pygame.display.flip()

        self.state = LoaderState()
        self._started_at = time.monotonic()
        self._done_at: float | None = None
        self._running = True
        self._cancel_rect = Rect(
            (self.width - CANCEL_BTN_W) // 2,
            self.height - 72,
            CANCEL_BTN_W,
            CANCEL_BTN_H,
        )

        try:
            LOADER_STDERR_LOG.write_text("", encoding="utf-8")
        except OSError:
            pass
        cmd = [
            sys.executable,
            "-u",
            str(CALIBRATE_SCRIPT),
            "--progress-json",
            "--no-restore-services",
            *calibrate_args,
        ]
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

    def _cancel_calibration(self) -> None:
        if self.state.cancelled or self.state.finished:
            return
        self.state.cancelled = True
        self.state.phase = "cancelling"
        self.state.message = "Cancelling…"
        self.reader.terminate()
        self.reader.join()
        self._drain_events()
        self.state.phase = "cancelled"
        self.state.message = format_cancel_message(
            saved=self.state.updated,
            attempted=self.state.index,
        )
        self.state.exit_code = 130
        self.state.finished = True
        self._done_at = time.monotonic()

    def _draw_button(self, rect: Rect, label: str, *, accent: bool = False) -> None:
        color = self.theme.accent if accent else self.theme.surface_alt
        pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=8)
        text_color = self.theme.bg if accent else self.theme.text
        draw_wrapped_text_in_rect(
            self.screen,
            self.font_md,
            label,
            rect.x,
            rect.y,
            rect.w,
            rect.h,
            text_color,
            pad_x=8,
            line_spacing=2,
            max_lines=2,
            align="center",
        )

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

    def _blit_wrapped_centered(
        self,
        text: str,
        y: int,
        font: pygame.font.Font,
        color: tuple[int, int, int],
        *,
        max_width: int | None = None,
        max_lines: int = 3,
        line_spacing: int = 4,
    ) -> int:
        wrap_w = max_width if max_width is not None else self.width - 80
        lines = wrap_text_lines(font, text, wrap_w, max_lines=max_lines)
        block_h = text_block_height(font, len(lines), line_spacing=line_spacing)
        start_y = y
        for i, line in enumerate(lines):
            surf = font.render(line, True, color)
            x = (self.width - surf.get_width()) // 2
            ty = start_y + i * (font.get_linesize() + line_spacing)
            self.screen.blit(surf, (x, ty))
        return start_y + block_h

    def _handle_pointer_down(self, pos: tuple[int, int]) -> None:
        if self.state.finished:
            return
        if self.state.phase in ("preparing", "calibrating", "cancelling"):
            if self._cancel_rect.contains(*pos):
                self._cancel_calibration()

    def _draw(self) -> None:
        self.screen.fill(self.theme.bg)
        self.state.elapsed_s = self._elapsed()

        title_text = "Calibrating patches…"
        if self.state.phase == "cancelled":
            title_text = "Calibration cancelled"
        elif self.state.phase == "cancelling":
            title_text = "Cancelling…"
        title = self.font_title.render(title_text, True, self.theme.text)
        self.screen.blit(title, ((self.width - title.get_width()) // 2, 72))

        if self.state.phase == "preparing":
            self._blit_wrapped_centered(
                self.state.message, 200, self.font_md, self.theme.muted
            )
        elif self.state.phase in ("error", "cancelled"):
            self._blit_wrapped_centered(
                self.state.message,
                200,
                self.font_md,
                theme_semantic_color(self.theme, "danger"),
                max_lines=4,
            )
        elif self.state.phase == "done":
            self._blit_wrapped_centered(
                self.state.message,
                200,
                self.font_md,
                theme_semantic_color(self.theme, "ok"),
                max_lines=3,
            )
        elif self.state.phase == "cancelling":
            self._blit_wrapped_centered(
                self.state.message, 200, self.font_md, self.theme.muted
            )
        else:
            name = self.state.patch_name or "…"
            name_y = self._blit_wrapped_centered(
                name, 168, self.font_md, self.theme.text, max_width=min(520, self.width - 80)
            )

            if self.state.total > 0:
                prog = self.font_md.render(
                    f"{self.state.index} / {self.state.total} · saved {self.state.updated}",
                    True,
                    self.theme.accent,
                )
                self.screen.blit(prog, ((self.width - prog.get_width()) // 2, name_y + 8))
                self._draw_progress_bar(name_y + 42, min(520, self.width - 80), 14)

        elapsed_s = self.font_sm.render(
            f"Elapsed {_format_elapsed(self.state.elapsed_s)}",
            True,
            self.theme.muted,
        )
        self.screen.blit(elapsed_s, ((self.width - elapsed_s.get_width()) // 2, 300))

        if self.state.phase in ("preparing", "calibrating"):
            warn = self.font_sm.render(
                "Do not touch — Surge is measuring loudness",
                True,
                self.theme.muted,
            )
            self.screen.blit(warn, ((self.width - warn.get_width()) // 2, self.height - 120))
            self._draw_button(self._cancel_rect, "Cancel")
        elif self.state.phase in ("done", "error", "cancelled"):
            hint = self.font_sm.render("Restarting patch browser…", True, self.theme.muted)
            self.screen.blit(hint, ((self.width - hint.get_width()) // 2, self.height - 56))

        pygame.display.flip()

    def run(self) -> int:
        clock = pygame.time.Clock()
        try:
            while self._running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        if not self.state.finished:
                            self._cancel_calibration()
                        else:
                            self._running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_q):
                            if not self.state.finished:
                                self._cancel_calibration()
                            else:
                                self._running = False
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        self._handle_pointer_down(event.pos)
                    elif event.type == pygame.FINGERDOWN:
                        x = int(event.x * self.width)
                        y = int(event.y * self.height)
                        self._handle_pointer_down((x, y))

                self._drain_events()
                if self.reader.proc.poll() is not None and not self.state.finished:
                    code = self.reader.proc.returncode or 0
                    if self.state.cancelled:
                        pass
                    elif self.state.phase != "error":
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
        finally:
            exit_code = self.state.exit_code
            if exit_code is None:
                exit_code = self.reader.join()
            if exit_code not in (0, 130):
                _write_loader_exit_report(self.state, subprocess_code=exit_code)
            draw_splash_frame(
                self.screen,
                mode=SplashMode.CAL_RETURN,
                theme=self.theme,
                progress=1.0,
            )
            time.sleep(CAL_RETURN_HOLD_SECONDS)
            pygame.quit()
            restore_mpe_audio_services(restart_browser=not calibration_from_browser())
            if calibration_from_browser():
                exec_touch_patch_browser()

        return exit_code if exit_code is not None else 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not CALIBRATE_SCRIPT.is_file():
        print(f"Missing calibrator: {CALIBRATE_SCRIPT}", file=sys.stderr)
        return 1

    preopened: pygame.Surface | None = None
    windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
    if not windowed and not os.environ.get("DISPLAY"):
        try:
            preopened, _ = paint_immediate(mode=SplashMode.CAL_ENTER)
        except RuntimeError:
            preopened = None

    return CalibrationLoaderApp(args, preopened_screen=preopened).run()


if __name__ == "__main__":
    raise SystemExit(main())
