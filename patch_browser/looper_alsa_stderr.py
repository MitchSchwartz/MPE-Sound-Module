"""Drain arecord/aplay stderr and count the xrun reports ALSA prints there.

The kernel exposes no per-substream xrun counter under /proc/asound, so the
messages these tools write to stderr are the only trustworthy underrun signal
the looper has. Draining matters for its own sake too: an unread pipe fills at
64 KB and then blocks the writer, which for aplay means it stops consuming
audio entirely.

Parsing is a pure function so tests never need a thread or a subprocess.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# aplay/arecord emit e.g. "underrun!!! (at least 21.333 ms long)".
_XRUN_RE = re.compile(r"\b(?:underrun|overrun)\b", re.IGNORECASE)
_XRUN_MS_RE = re.compile(r"at least\s+([0-9]+(?:\.[0-9]+)?)\s*ms", re.IGNORECASE)

# Without -q both tools print a format header; echo a few lines then stay quiet
# so a misconfigured device can't flood the journal.
MAX_ECHOED_LINES = 20


def parse_xrun_line(line: str) -> float | None:
    """Reported xrun length in ms (0.0 when unstated), or None if not an xrun line."""
    if not _XRUN_RE.search(line):
        return None
    match = _XRUN_MS_RE.search(line)
    return float(match.group(1)) if match else 0.0


@dataclass(frozen=True)
class XrunWindow:
    """Xrun activity over one reporting window."""

    count: int = 0
    worst_ms: float = 0.0
    total_ms: float = 0.0

    @property
    def is_clean(self) -> bool:
        return self.count == 0

    def __str__(self) -> str:
        if not self.count:
            return "none"
        return f"{self.count}(worst={self.worst_ms:.1f}ms total={self.total_ms:.1f}ms)"


class AlsaStderrMonitor:
    """Background drain of one process's stderr, counting xrun reports."""

    def __init__(
        self,
        label: str,
        stream: object | None,
        *,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        self.label = label
        self._stream = stream
        self._echo = echo if echo is not None else _default_echo
        self._lock = threading.Lock()
        self._count = 0
        self._worst_ms = 0.0
        self._total_ms = 0.0
        self._session_count = 0
        self._echoed = 0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._stream is None or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._pump,
            name=f"alsa-stderr-{self.label}",
            daemon=True,
        )
        self._thread.start()

    def _pump(self) -> None:
        readline = getattr(self._stream, "readline", None)
        if readline is None:
            return
        for raw in iter(readline, b""):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            self.feed(raw)

    def feed(self, line: str) -> None:
        """Account one stderr line."""
        text = line.strip()
        if not text:
            return
        length_ms = parse_xrun_line(text)
        if length_ms is None:
            if self._echoed < MAX_ECHOED_LINES:
                self._echoed += 1
                self._echo(f"[{self.label}] {text}")
            return
        with self._lock:
            self._count += 1
            self._session_count += 1
            self._total_ms += length_ms
            if length_ms > self._worst_ms:
                self._worst_ms = length_ms

    def take_window(self) -> XrunWindow:
        """Xruns since the previous call, resetting the window counters."""
        with self._lock:
            window = XrunWindow(self._count, self._worst_ms, self._total_ms)
            self._count = 0
            self._worst_ms = 0.0
            self._total_ms = 0.0
        return window

    @property
    def session_xruns(self) -> int:
        with self._lock:
            return self._session_count


def _default_echo(message: str) -> None:
    print(message, flush=True)


def start_alsa_stderr_monitors(
    *procs: tuple[str, object],
    echo: Callable[[str], None] | None = None,
) -> list[AlsaStderrMonitor]:
    """Start a drain thread per process. Call only after start-up checks read stderr."""
    monitors = [
        AlsaStderrMonitor(label, getattr(proc, "stderr", None), echo=echo)
        for label, proc in procs
    ]
    for monitor in monitors:
        monitor.start()
    return monitors


def format_xrun_report(monitors: Sequence[AlsaStderrMonitor]) -> str:
    """One-line per-stream summary; consumes each monitor's window."""
    return " ".join(f"{m.label}={m.take_window()}" for m in monitors)


def session_xrun_total(monitors: Sequence[AlsaStderrMonitor]) -> int:
    return sum(m.session_xruns for m in monitors)
