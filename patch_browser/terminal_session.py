"""PTY-backed terminal for the DSI panel (#113 Phase 2).

Why a PTY rendered inside pygame rather than a real VT:

* The app never tears down, so nothing has to hand DRM back and forth. The
  shutdown path already shows how expensive that handoff is — the splash unit
  can block ~12s retrying while the browser still holds DRM — and
  ``release_display_for_shutdown()`` is one-way as written (it calls
  ``pygame.quit()`` and stops getty on tty1, which is the console a VT would
  need running).
* Audio is unaffected either way. Confirmed 2026-08-26 on the Pi 5: the browser
  is not a JACK client (``jack_lsp`` lists system, Surge XT, mpe-peak-meter and
  mpe-looper, and the unit file states the pygame process must never register a
  JACK process callback). A debug shell is most wanted *while* something
  misbehaves, so this had to be checked rather than assumed.
* Staying in-process means the way home is ours to guarantee. See
  ``TerminalSession.exited``.

Everything in this module is display-free and testable headless. The pygame view
lives in ``touch_browser_terminal.py``.
"""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import termios

DEFAULT_ROWS = 24
DEFAULT_COLS = 80

# Read budget per frame. The shell can produce output far faster than 60 fps can
# draw it (`find /`, a big `cat`), and an unbounded drain would stall the UI
# thread for as long as the producer keeps writing. Dropping frames of scrollback
# is fine; freezing the only interface on the device is not.
READ_CHUNK = 8192
MAX_READ_PER_TICK = 64 * 1024


class TerminalBuffer:
    """A character grid with a cursor and a small ANSI subset.

    Deliberately not a full VT100. This exists to run ``systemctl status``,
    ``journalctl``, ``jack_lsp`` and friends on a 5" panel — the escapes those
    actually emit are cursor motion, erase, and colour. Colour is parsed and
    discarded rather than rendered: on 800x480 the win is legibility, and a
    half-implemented palette is worse than none.
    """

    def __init__(self, rows: int = DEFAULT_ROWS, cols: int = DEFAULT_COLS) -> None:
        self.rows = max(1, rows)
        self.cols = max(1, cols)
        self.grid: list[list[str]] = [[" "] * self.cols for _ in range(self.rows)]
        self.cur_row = 0
        self.cur_col = 0
        self._pending = ""

    # -- geometry ---------------------------------------------------------
    def resize(self, rows: int, cols: int) -> None:
        rows = max(1, rows)
        cols = max(1, cols)
        if rows == self.rows and cols == self.cols:
            return
        old = self.grid
        self.grid = [[" "] * cols for _ in range(rows)]
        # Keep the BOTTOM of the old buffer: the prompt and the most recent
        # output are what the user is looking at.
        keep = old[-rows:]
        for r, line in enumerate(keep):
            self.grid[r][: min(cols, len(line))] = line[:cols]
        self.rows, self.cols = rows, cols
        self.cur_row = min(self.cur_row, rows - 1)
        self.cur_col = min(self.cur_col, cols - 1)

    def text_lines(self) -> list[str]:
        return ["".join(row).rstrip() for row in self.grid]

    # -- writing ----------------------------------------------------------
    def _scroll(self) -> None:
        self.grid.pop(0)
        self.grid.append([" "] * self.cols)

    def _newline(self) -> None:
        self.cur_row += 1
        if self.cur_row >= self.rows:
            self.cur_row = self.rows - 1
            self._scroll()

    def _put(self, ch: str) -> None:
        if self.cur_col >= self.cols:
            self.cur_col = 0
            self._newline()
        self.grid[self.cur_row][self.cur_col] = ch
        self.cur_col += 1

    def _erase_in_display(self, mode: int) -> None:
        if mode == 2:
            self.grid = [[" "] * self.cols for _ in range(self.rows)]
            return
        if mode == 0:  # cursor to end
            self.grid[self.cur_row][self.cur_col:] = [" "] * (self.cols - self.cur_col)
            for r in range(self.cur_row + 1, self.rows):
                self.grid[r] = [" "] * self.cols
        elif mode == 1:  # start to cursor
            for r in range(self.cur_row):
                self.grid[r] = [" "] * self.cols
            self.grid[self.cur_row][: self.cur_col + 1] = [" "] * (self.cur_col + 1)

    def _erase_in_line(self, mode: int) -> None:
        if mode == 0:
            self.grid[self.cur_row][self.cur_col:] = [" "] * (self.cols - self.cur_col)
        elif mode == 1:
            self.grid[self.cur_row][: self.cur_col + 1] = [" "] * (self.cur_col + 1)
        elif mode == 2:
            self.grid[self.cur_row] = [" "] * self.cols

    def _csi(self, params: str, final: str) -> None:
        nums = [int(p) for p in params.split(";") if p.isdigit()]

        def n(idx: int, default: int = 1) -> int:
            return nums[idx] if idx < len(nums) else default

        if final == "A":
            self.cur_row = max(0, self.cur_row - n(0))
        elif final == "B":
            self.cur_row = min(self.rows - 1, self.cur_row + n(0))
        elif final == "C":
            self.cur_col = min(self.cols - 1, self.cur_col + n(0))
        elif final == "D":
            self.cur_col = max(0, self.cur_col - n(0))
        elif final in ("H", "f"):
            self.cur_row = min(self.rows - 1, max(0, n(0) - 1))
            self.cur_col = min(self.cols - 1, max(0, n(1) - 1))
        elif final == "G":
            self.cur_col = min(self.cols - 1, max(0, n(0) - 1))
        elif final == "J":
            self._erase_in_display(n(0, 0))
        elif final == "K":
            self._erase_in_line(n(0, 0))
        # m (SGR), h/l (modes), and everything else are consumed and ignored.

    def feed(self, text: str) -> None:
        """Consume terminal output. Safe to call with a split escape sequence —
        the remainder is held until the rest arrives, which matters because PTY
        reads chop escapes at arbitrary byte boundaries."""
        data = self._pending + text
        self._pending = ""
        i = 0
        n = len(data)
        while i < n:
            ch = data[i]
            if ch == "\x1b":
                if i + 1 >= n:
                    self._pending = data[i:]
                    return
                nxt = data[i + 1]
                if nxt == "[":
                    j = i + 2
                    while j < n and not ("@" <= data[j] <= "~"):
                        j += 1
                    if j >= n:
                        self._pending = data[i:]
                        return
                    self._csi(data[i + 2:j], data[j])
                    i = j + 1
                    continue
                if nxt in ("]", "P", "^", "_"):
                    # OSC and friends: run to BEL or ST. Titles, mostly.
                    j = i + 2
                    while j < n and data[j] != "\x07" and data[j:j + 2] != "\x1b\\":
                        j += 1
                    if j >= n:
                        self._pending = data[i:]
                        return
                    i = j + (2 if data[j:j + 2] == "\x1b\\" else 1)
                    continue
                i += 2  # two-character escape (charset select, etc.)
                continue
            if ch == "\n":
                self._newline()
            elif ch == "\r":
                self.cur_col = 0
            elif ch == "\b":
                self.cur_col = max(0, self.cur_col - 1)
            elif ch == "\t":
                self.cur_col = min(self.cols - 1, (self.cur_col // 8 + 1) * 8)
            elif ch == "\x07":
                pass  # BEL: no speaker to ring, and no visual bell on an instrument
            elif ch >= " ":
                self._put(ch)
            i += 1


class TerminalSession:
    """A shell on a PTY, read cooperatively from the UI loop.

    ``exited`` is the way home and the reason this is safe to ship: when the
    shell ends — ``exit``, Ctrl+D, or a crash inside it — the session reports it
    and the app returns to the browser. The return path is therefore the shell's
    own lifecycle rather than a separate mechanism that could itself fail.
    """

    def __init__(self, rows: int = DEFAULT_ROWS, cols: int = DEFAULT_COLS,
                 *, shell: str | None = None, cwd: str | None = None) -> None:
        self.buffer = TerminalBuffer(rows, cols)
        self.pid: int | None = None
        self.fd: int | None = None
        self.exited = False
        self._shell = shell or os.environ.get("SHELL") or "/bin/bash"
        self._cwd = cwd

    def start(self) -> None:
        if self.pid is not None:
            return
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                if self._cwd:
                    os.chdir(self._cwd)
                os.environ["TERM"] = "vt100"
                # No colour: the buffer parses SGR and throws it away, so a
                # coloured prompt would only cost bytes.
                os.environ.pop("LS_COLORS", None)
                os.execvp(self._shell, [self._shell])
            except Exception:
                os._exit(127)
        self.pid = pid
        self.fd = fd
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._set_winsize()

    def _set_winsize(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.ioctl(
                self.fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", self.buffer.rows, self.buffer.cols, 0, 0),
            )
        except OSError:
            pass

    def resize(self, rows: int, cols: int) -> None:
        self.buffer.resize(rows, cols)
        self._set_winsize()

    def write(self, data: bytes) -> None:
        if self.fd is None or self.exited or not data:
            return
        try:
            os.write(self.fd, data)
        except OSError:
            self.exited = True

    def pump(self) -> bool:
        """Drain available output into the buffer. Returns True if anything
        changed, so the UI can skip a redraw when the shell is idle."""
        if self.fd is None or self.exited:
            return False
        changed = False
        total = 0
        while total < MAX_READ_PER_TICK:
            try:
                chunk = os.read(self.fd, READ_CHUNK)
            except BlockingIOError:
                break
            except OSError:
                # EIO on Linux is the normal signal that the child closed the PTY.
                self.exited = True
                break
            if not chunk:
                self.exited = True
                break
            total += len(chunk)
            self.buffer.feed(chunk.decode("utf-8", errors="replace"))
            changed = True
        if not self.exited and self.pid is not None:
            try:
                done, _ = os.waitpid(self.pid, os.WNOHANG)
                if done == self.pid:
                    self.exited = True
            except ChildProcessError:
                self.exited = True
        return changed

    def stop(self) -> None:
        """Tear the shell down. Called on the way out, and on app shutdown so a
        forgotten session cannot outlive the UI that owns it."""
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except OSError:
                pass
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None
        self.pid = None
        self.exited = True


# -- key encoding ---------------------------------------------------------
#
# Pure, so the whole keyboard path is testable without a display. Values are
# SDL's; see keyboard_shortcuts.py for why this module does not import pygame.

_K_RETURN = 13
_K_ESCAPE = 27
_K_BACKSPACE = 8
_K_TAB = 9
_K_DELETE = 127
_K_UP, _K_DOWN, _K_RIGHT, _K_LEFT = 1073741906, 1073741905, 1073741903, 1073741904
_K_HOME, _K_END = 1073741898, 1073741901
_K_PAGEUP, _K_PAGEDOWN = 1073741899, 1073741902

_SPECIAL: dict[int, bytes] = {
    _K_RETURN: b"\r",
    _K_ESCAPE: b"\x1b",
    _K_TAB: b"\t",
    # Backspace sends DEL, not BS: that is what termios expects as the erase
    # character on Linux, and getting it wrong makes backspace print ^H.
    _K_BACKSPACE: b"\x7f",
    _K_DELETE: b"\x1b[3~",
    _K_UP: b"\x1b[A",
    _K_DOWN: b"\x1b[B",
    _K_RIGHT: b"\x1b[C",
    _K_LEFT: b"\x1b[D",
    _K_HOME: b"\x1b[H",
    _K_END: b"\x1b[F",
    _K_PAGEUP: b"\x1b[5~",
    _K_PAGEDOWN: b"\x1b[6~",
}

_KMOD_CTRL = 0x00C0


def key_to_bytes(key: int, mods: int, unicode_ch: str = "") -> bytes:
    """Encode a KEYDOWN for the shell. Empty bytes means "send nothing".

    Ctrl+letter is handled explicitly rather than trusting the event's unicode:
    SDL does deliver control characters there, but Ctrl+C is the one key on this
    device that has to work when the shell is misbehaving, and that is not a
    thing to leave to a platform detail.
    """
    if mods & _KMOD_CTRL and 0x61 <= key <= 0x7A:  # ctrl+a .. ctrl+z
        return bytes([key & 0x1F])
    special = _SPECIAL.get(key)
    if special is not None:
        return special
    if unicode_ch and (unicode_ch >= " " or unicode_ch == "\t"):
        return unicode_ch.encode("utf-8")
    return b""
