"""Terminal screen wiring (#113): chord routing, confirm gate, way home."""

from __future__ import annotations

import time
import unittest

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

import pygame  # noqa: E402

from patch_browser.keyboard_shortcuts import (  # noqa: E402
    ACTION_RESTART_BENCH,
    ACTION_TERMINAL,
    match_chord,
)
from patch_browser.terminal_session import TerminalSession  # noqa: E402
from patch_browser.touch_browser_input import TouchBrowserInputMixin  # noqa: E402
from patch_browser.touch_browser_terminal import TerminalMixin  # noqa: E402
from patch_browser.touch_ui_enums import Screen  # noqa: E402

CTRL_ALT = pygame.KMOD_LCTRL | pygame.KMOD_LALT


class _Stub(TerminalMixin, TouchBrowserInputMixin):
    """Only what the keyboard path touches."""

    def __init__(self) -> None:
        self.screen_state = Screen.BROWSER
        self._pending_confirm_kind = "calibrate"
        self._terminal = None
        self._terminal_last_input = 0.0
        self._terminal_esc_armed = False
        self.toasts: list[str] = []

    def _toast(self, message: str, _duration: float = 0.0) -> None:
        self.toasts.append(message)


def _key(k: int, mod: int = 0, unicode_ch: str = "") -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, {"key": k, "mod": mod, "unicode": unicode_ch})


class TestChordRouting(unittest.TestCase):
    def test_ctrl_alt_t_asks_before_opening(self) -> None:
        """The chord must not open a shell outright — a device that enumerates
        as a keyboard could otherwise fire it mid-set."""
        app = _Stub()
        self.assertTrue(app._handle_keydown(_key(pygame.K_t, CTRL_ALT)))
        self.assertEqual(app.screen_state, Screen.CALIBRATE_CONFIRM)
        self.assertEqual(app._pending_confirm_kind, "terminal")
        self.assertIsNone(app._terminal)

    def test_ctrl_alt_r_asks_before_restarting(self) -> None:
        app = _Stub()
        self.assertTrue(app._handle_keydown(_key(pygame.K_r, CTRL_ALT)))
        self.assertEqual(app.screen_state, Screen.CALIBRATE_CONFIRM)
        self.assertEqual(app._pending_confirm_kind, "restart_bench")

    def test_unrelated_key_is_not_consumed(self) -> None:
        app = _Stub()
        self.assertFalse(app._handle_keydown(_key(pygame.K_t, 0, "t")))
        self.assertEqual(app.screen_state, Screen.BROWSER)

    def test_chords_are_distinct_actions(self) -> None:
        self.assertNotEqual(
            match_chord(pygame.K_t, CTRL_ALT), match_chord(pygame.K_r, CTRL_ALT)
        )
        self.assertEqual(match_chord(pygame.K_t, CTRL_ALT), ACTION_TERMINAL)
        self.assertEqual(match_chord(pygame.K_r, CTRL_ALT), ACTION_RESTART_BENCH)


class TestWayHome(unittest.TestCase):
    """Every exit from the terminal, because being unable to leave is the one
    failure this feature must not have on a device whose only display it owns."""

    def _open(self) -> _Stub:
        app = _Stub()
        app.screen_state = Screen.TERMINAL
        app._terminal = TerminalSession(rows=6, cols=30, shell="/bin/sh")
        app._terminal.start()
        app._terminal_last_input = time.monotonic()
        return app

    def test_chord_closes_a_wedged_shell(self) -> None:
        """Ctrl+Alt+T is checked BEFORE the shell sees the key, so a process
        that swallows input cannot hold the panel."""
        app = self._open()
        try:
            self.assertTrue(app._handle_keydown(_key(pygame.K_t, CTRL_ALT)))
            self.assertIsNone(app._terminal)
            self.assertEqual(app.screen_state, Screen.BROWSER)
        finally:
            if app._terminal:
                app._terminal.stop()

    def test_double_escape_closes(self) -> None:
        app = self._open()
        try:
            app._handle_keydown(_key(pygame.K_ESCAPE))
            self.assertEqual(app.screen_state, Screen.TERMINAL, "one Esc is passed through")
            app._handle_keydown(_key(pygame.K_ESCAPE))
            self.assertEqual(app.screen_state, Screen.BROWSER)
        finally:
            if app._terminal:
                app._terminal.stop()

    def test_single_escape_then_typing_does_not_close(self) -> None:
        """Esc is a real key inside vi and less; the force-close must need two
        in a row or it fires while someone is editing a file."""
        app = self._open()
        try:
            app._handle_keydown(_key(pygame.K_ESCAPE))
            app._handle_keydown(_key(ord("a"), 0, "a"))
            app._handle_keydown(_key(pygame.K_ESCAPE))
            self.assertEqual(app.screen_state, Screen.TERMINAL)
        finally:
            if app._terminal:
                app._terminal.stop()

    def test_shell_exit_returns_to_browser(self) -> None:
        app = self._open()
        try:
            app._terminal.write(b"exit\n")
            end = time.time() + 3.0
            while time.time() < end and app.screen_state == Screen.TERMINAL:
                app._poll_terminal()
                time.sleep(0.02)
            self.assertEqual(app.screen_state, Screen.BROWSER)
            self.assertIn("Shell exited", app.toasts)
        finally:
            if app._terminal:
                app._terminal.stop()

    def test_idle_watchdog_closes(self) -> None:
        """Backstop for a crash inside the terminal, or a keyboard unplugged
        while it is open."""
        from patch_browser import touch_browser_terminal as tt

        app = self._open()
        try:
            app._terminal_last_input = -(tt.TERMINAL_IDLE_TIMEOUT_S + 60.0)
            app._poll_terminal()
            self.assertEqual(app.screen_state, Screen.BROWSER)
            self.assertIn("Terminal closed (idle)", app.toasts)
        finally:
            if app._terminal:
                app._terminal.stop()


if __name__ == "__main__":
    unittest.main()
