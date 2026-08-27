"""Terminal screen for the touch browser (#113).

Mixin split out of the draw/input modules because the terminal is the one screen
with a live subprocess behind it: its lifecycle, not just its pixels, has to be
owned somewhere legible.

The way home, in order of what fires first:

1. ``exit`` / Ctrl+D — the shell's own lifecycle. Primary, and the one every
   Unix user already knows.
2. Ctrl+Alt+T again, or Esc twice — closes the session from the app side, for a
   shell that is wedged rather than exited.
3. An idle watchdog — restores the browser after
   ``TERMINAL_IDLE_TIMEOUT_S`` with no keypress, so a crash *inside* the
   terminal, or a keyboard unplugged while it is open, cannot hold the panel.

(3) is the one that makes this safe to ship. Rule -1 applies to the interface as
much as to the audio: the instrument must not be able to end up in a state it
cannot leave, and on this device the display is the only way out.
"""

from __future__ import annotations

import time

import pygame

from patch_browser.terminal_session import TerminalSession, key_to_bytes
from patch_browser.touch_ui_enums import Screen

TERMINAL_IDLE_TIMEOUT_S = 300.0
TERMINAL_PAD = 6
TERMINAL_FOOTER_H = 18


class TerminalMixin:
    def _terminal_metrics(self) -> tuple[int, int, int, int]:
        """(rows, cols, glyph_w, line_h) for the current panel and font."""
        font = self._terminal_font()
        glyph_w = max(1, font.size("M")[0])
        line_h = max(1, font.get_linesize())
        usable_h = self.height - TERMINAL_PAD * 2 - TERMINAL_FOOTER_H
        cols = max(20, (self.width - TERMINAL_PAD * 2) // glyph_w)
        rows = max(4, usable_h // line_h)
        return rows, cols, glyph_w, line_h

    def _terminal_font(self):
        font = getattr(self, "_term_font", None)
        if font is None:
            # A real monospace face, or the grid does not line up. SysFont falls
            # back to the default face when none of the names resolve, which is
            # ugly but still renders — better than refusing to open a shell.
            font = pygame.font.SysFont("dejavusansmono,liberationmono,monospace", 13)
            self._term_font = font
        return font

    def _open_terminal(self) -> None:
        if getattr(self, "_terminal", None) is not None:
            return
        rows, cols, _, _ = self._terminal_metrics()
        session = TerminalSession(rows=rows, cols=cols, cwd=str(getattr(self, "repo_root", "") or "") or None)
        try:
            session.start()
        except OSError as exc:
            self._toast(f"Terminal failed: {exc}", 4.0)
            return
        self._terminal = session
        self._terminal_last_input = time.monotonic()
        self._terminal_esc_armed = False
        self.screen_state = Screen.TERMINAL

    def _close_terminal(self, message: str | None = None) -> None:
        session = getattr(self, "_terminal", None)
        if session is not None:
            session.stop()
        self._terminal = None
        self._terminal_esc_armed = False
        if self.screen_state == Screen.TERMINAL:
            self.screen_state = Screen.BROWSER
        if message:
            self._toast(message, 3.0)

    def _poll_terminal(self) -> bool:
        """Returns True when the screen needs redrawing."""
        session = getattr(self, "_terminal", None)
        if session is None:
            return False
        changed = session.pump()
        if session.exited:
            self._close_terminal("Shell exited")
            return True
        now = time.monotonic()
        idle = now - getattr(self, "_terminal_last_input", now)
        if idle > TERMINAL_IDLE_TIMEOUT_S:
            self._close_terminal("Terminal closed (idle)")
            return True
        return changed

    def _handle_terminal_key(self, event: pygame.event.Event) -> None:
        session = getattr(self, "_terminal", None)
        if session is None:
            return
        self._terminal_last_input = time.monotonic()

        # Esc twice is the app-side escape hatch for a shell that will not exit.
        # Once is passed through, because Esc is a real key inside vi and less.
        if event.key == pygame.K_ESCAPE:
            if getattr(self, "_terminal_esc_armed", False):
                self._close_terminal("Terminal closed")
                return
            self._terminal_esc_armed = True
        else:
            self._terminal_esc_armed = False

        data = key_to_bytes(event.key, event.mod, getattr(event, "unicode", "") or "")
        if data:
            session.write(data)

    def _draw_terminal(self) -> None:
        session = getattr(self, "_terminal", None)
        if session is None:
            return
        font = self._terminal_font()
        rows, cols, glyph_w, line_h = self._terminal_metrics()
        if (rows, cols) != (session.buffer.rows, session.buffer.cols):
            session.resize(rows, cols)

        self.screen.fill(self.theme.bg)
        lines = session.buffer.text_lines()
        for r, line in enumerate(lines[:rows]):
            if not line:
                continue
            self.screen.blit(
                font.render(line, True, self.theme.text),
                (TERMINAL_PAD, TERMINAL_PAD + r * line_h),
            )

        # Block cursor. On a panel this size a thin bar is genuinely hard to find.
        cur_x = TERMINAL_PAD + session.buffer.cur_col * glyph_w
        cur_y = TERMINAL_PAD + session.buffer.cur_row * line_h
        cursor = pygame.Surface((glyph_w, line_h), pygame.SRCALPHA)
        c = pygame.Color(self.theme.accent)
        cursor.fill((c.r, c.g, c.b, 110))
        self.screen.blit(cursor, (cur_x, cur_y))

        # The footer is not decoration: it is the standing answer to "how do I
        # get out", visible at all times so it cannot be forgotten or scrolled
        # away. Removing it re-opens the stranding risk this design closed.
        hint = font.render(
            "exit / Ctrl+D to return    Esc Esc to force close",
            True,
            self.theme.muted,
        )
        self.screen.blit(hint, (TERMINAL_PAD, self.height - TERMINAL_FOOTER_H))
