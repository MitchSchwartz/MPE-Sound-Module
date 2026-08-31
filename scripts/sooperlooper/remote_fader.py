"""Inbound fader moves from another process, replayed as if they were CCs.

The touch UI's Vol fader and the APC's master fader mean the same thing, but
they live in different processes: the fader surface is in
`mpe-looper-session.service` and the touch screen is in
`touch-patch-browser.service`. `LoopMix` — and therefore the master gain that
`wet_for()` multiplies — is only reachable from the first.

The obvious shortcut is to let the touch UI write `wet` directly, the way
`looper_songs.load_song` is permitted to. That exception is safe because it
fires once, at song load, and `LoopMix.seed_from_engine` adopts the value
afterwards. A volume fader is not once — it is a continuous drag, and a second
continuous writer is exactly the drift `loop_mix` was built to make impossible:
`seed_from_engine` would keep back-computing column gains from a master that
the other process is still moving, and the corruption would look like a
hardware fault.

So nothing here composes or writes a level. This is a *transport*: it carries a
master fader position across the process boundary, and the bench feeds it into
the same `handle_cc` path the hardware fader uses. One writer, one composition
point, one more surface.

Wire format is one line of ASCII per datagram, `master <0-127>`, deliberately
not OSC: the receiver must be drainable without a thread (see below), and a
text line keeps the touch UI's side a three-line `sendto` with no dependency on
pythonosc being importable there.

The socket is non-blocking and drained from the bench's existing idle branch
rather than served on a thread. A thread would be one more scheduler client in
a process whose whole job is to not be late; a `recvfrom` on an empty
non-blocking socket is a single syscall, and the idle branch already sleeps 2 ms
between passes.
"""

from __future__ import annotations

import errno
import os
import socket

#: Only the master is carried today. Per-column moves stay on the APC, which is
#: the surface that has pickup state for them; a touch column fader would need
#: its own pickup story before it could use this.
MASTER_KEY = "master"

CC_MIN = 0
CC_MAX = 127

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9956

#: Cap on datagrams drained per pass, so a flood cannot hold the loop. A drag
#: is far below this; anything above it is a fault, and dropping is correct
#: because only the newest position matters.
MAX_DRAIN = 64


def resolve_port() -> int:
    raw = os.environ.get("MPE_LOOPER_CTL_PORT", "")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def parse_message(data: bytes) -> int | None:
    """`b"master 96"` -> 96. Anything else -> None, silently.

    Silently, because this socket is reachable by anything on loopback and a
    malformed datagram must not be able to stop the fader surface.
    """
    try:
        text = data.decode("ascii", "strict").strip()
    except UnicodeDecodeError:
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] != MASTER_KEY:
        return None
    try:
        value = int(parts[1])
    except ValueError:
        return None
    if not CC_MIN <= value <= CC_MAX:
        return None
    return value


def level_to_cc(level: float) -> int:
    """0.0-1.0 -> 0-127, clamped. The touch UI's Vol scale is 0-1."""
    try:
        scaled = round(float(level) * CC_MAX)
    except (TypeError, ValueError):
        return CC_MAX
    return max(CC_MIN, min(CC_MAX, scaled))


class RemoteFaderReceiver:
    """Non-blocking UDP source of master fader positions.

    `open()` never raises: a port already in use means the fader surface runs
    without remote volume, which is a degraded feature, not a dead looper.
    """

    def __init__(self, *, host: str = DEFAULT_HOST, port: int | None = None) -> None:
        self.host = host
        self.port = resolve_port() if port is None else port
        self._sock: socket.socket | None = None
        self.error: str | None = None

    def open(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.bind((self.host, self.port))
        except OSError as exc:
            self.error = f"{exc}"
            self._sock = None
            return False
        self._sock = sock
        self.error = None
        return True

    def poll(self) -> int | None:
        """Newest valid master position since the last poll, or None.

        Coalescing here rather than replaying every datagram: intermediate
        positions of a drag that already arrived are stale by definition, and
        the sender is rate-limited anyway.
        """
        if self._sock is None:
            return None
        latest: int | None = None
        for _ in range(MAX_DRAIN):
            try:
                data, _addr = self._sock.recvfrom(64)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ECONNREFUSED):
                    break
                break
            value = parse_message(data)
            if value is not None:
                latest = value
        return latest

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


def send_master(level: float, *, host: str = DEFAULT_HOST, port: int | None = None) -> bool:
    """Fire-and-forget a 0.0-1.0 level at the fader surface.

    Returns False rather than raising when the looper is not running: the touch
    UI's volume must keep working on Surge alone.
    """
    target = resolve_port() if port is None else port
    payload = f"{MASTER_KEY} {level_to_cc(level)}".encode("ascii")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            sock.sendto(payload, (host, target))
    except OSError:
        return False
    return True
