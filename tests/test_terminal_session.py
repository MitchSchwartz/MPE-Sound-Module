"""Terminal core for the DSI shell (#113 Phase 2). No display required."""

from __future__ import annotations

import time
import unittest

from patch_browser.terminal_session import (
    TerminalBuffer,
    TerminalSession,
    key_to_bytes,
)

KMOD_CTRL = 0x0040


class TestTerminalBuffer(unittest.TestCase):
    def test_plain_text_and_newline(self) -> None:
        b = TerminalBuffer(3, 10)
        b.feed("ab\r\ncd")
        self.assertEqual(b.text_lines()[:2], ["ab", "cd"])

    def test_wrap_at_right_margin(self) -> None:
        b = TerminalBuffer(3, 4)
        b.feed("abcdef")
        self.assertEqual(b.text_lines()[:2], ["abcd", "ef"])

    def test_scroll_keeps_the_bottom(self) -> None:
        b = TerminalBuffer(2, 5)
        b.feed("one\r\ntwo\r\nthree")
        self.assertEqual(b.text_lines(), ["two", "three"])

    def test_backspace_and_carriage_return(self) -> None:
        b = TerminalBuffer(1, 10)
        b.feed("abc\b \rZ")
        self.assertEqual(b.text_lines()[0], "Zb")

    def test_cursor_addressing_and_erase(self) -> None:
        b = TerminalBuffer(3, 6)
        b.feed("aaa\r\nbbb\x1b[1;1Hz")
        self.assertEqual(b.text_lines()[0], "zaa")
        b.feed("\x1b[2J")
        self.assertEqual(b.text_lines(), ["", "", ""])

    def test_sgr_colour_is_consumed_not_printed(self) -> None:
        b = TerminalBuffer(1, 20)
        b.feed("\x1b[31mred\x1b[0m")
        self.assertEqual(b.text_lines()[0], "red")

    def test_osc_title_is_consumed(self) -> None:
        b = TerminalBuffer(1, 20)
        b.feed("\x1b]0;my title\x07ok")
        self.assertEqual(b.text_lines()[0], "ok")

    def test_escape_split_across_reads(self) -> None:
        """PTY reads chop escapes at arbitrary byte boundaries; a naive parser
        prints the fragment as text and corrupts the screen."""
        b = TerminalBuffer(2, 8)
        b.feed("x\x1b[")
        b.feed("2J")
        self.assertEqual(b.text_lines(), ["", ""])

    def test_resize_keeps_recent_output(self) -> None:
        b = TerminalBuffer(4, 10)
        b.feed("one\r\ntwo\r\nthree\r\nfour")
        b.resize(2, 10)
        self.assertEqual(b.text_lines(), ["three", "four"])


class TestKeyEncoding(unittest.TestCase):
    def test_ctrl_c_and_ctrl_d(self) -> None:
        """Ctrl+C must work when the shell is misbehaving — that is the whole
        point of having a terminal on the device."""
        self.assertEqual(key_to_bytes(ord("c"), KMOD_CTRL), b"\x03")
        self.assertEqual(key_to_bytes(ord("d"), KMOD_CTRL), b"\x04")

    def test_backspace_sends_del(self) -> None:
        self.assertEqual(key_to_bytes(8, 0), b"\x7f")

    def test_arrows(self) -> None:
        self.assertEqual(key_to_bytes(1073741906, 0), b"\x1b[A")
        self.assertEqual(key_to_bytes(1073741904, 0), b"\x1b[D")

    def test_printable_uses_unicode(self) -> None:
        self.assertEqual(key_to_bytes(ord("a"), 0, "a"), b"a")
        self.assertEqual(key_to_bytes(ord("a"), 0x0001, "A"), b"A")

    def test_unknown_key_sends_nothing(self) -> None:
        self.assertEqual(key_to_bytes(1073742048, 0), b"")


class TestTerminalSession(unittest.TestCase):
    """Real PTY round-trip. Cheap, and the parts that break in production
    (nonblocking reads, EIO on child exit) cannot be exercised any other way."""

    def _drain(self, s: TerminalSession, deadline: float = 3.0) -> None:
        end = time.time() + deadline
        while time.time() < end:
            s.pump()
            if s.exited:
                return
            time.sleep(0.02)

    def test_command_output_reaches_the_buffer(self) -> None:
        s = TerminalSession(rows=10, cols=40, shell="/bin/sh")
        s.start()
        try:
            s.write(b"echo hello-from-pty\n")
            end = time.time() + 3.0
            while time.time() < end:
                s.pump()
                if any("hello-from-pty" in ln for ln in s.buffer.text_lines()):
                    break
                time.sleep(0.02)
            self.assertTrue(
                any("hello-from-pty" in ln for ln in s.buffer.text_lines()),
                s.buffer.text_lines(),
            )
        finally:
            s.stop()

    def test_exit_marks_the_session_exited(self) -> None:
        """This is the return path. If it does not fire, the user is stranded."""
        s = TerminalSession(rows=10, cols=40, shell="/bin/sh")
        s.start()
        try:
            s.write(b"exit\n")
            self._drain(s)
            self.assertTrue(s.exited)
        finally:
            s.stop()

    def test_pump_before_start_is_inert(self) -> None:
        s = TerminalSession()
        self.assertFalse(s.pump())

    def test_write_after_stop_does_not_raise(self) -> None:
        s = TerminalSession(shell="/bin/sh")
        s.start()
        s.stop()
        s.write(b"echo x\n")  # must not raise
        self.assertTrue(s.exited)


if __name__ == "__main__":
    unittest.main()
