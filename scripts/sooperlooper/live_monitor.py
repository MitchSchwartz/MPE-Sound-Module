"""How loud you hear yourself, while the loops decide nothing about it.

`native/mpe-live-monitor` is a gain stage on the monitor branch of the Surge
fan-out. It holds no policy: it is told an amplitude and applies it. This
module is the policy, and it is the only thing that decides that amplitude.

**The rule, in one line: while a track is capturing, you hear yourself at the
level that track is about to play back at; otherwise you hear yourself at the
live level.**

That is the record-arm behaviour of every DAW, and before that the monitor
path of a split console: pre-fader to tape, post-fader to the monitor mix. The
capture path is untouched by any of this, so a take is always recorded at full
scale no matter what you were monitoring at, and stays raisable afterwards.
That is the whole reason the gain stage is on this branch and not upstream of
the fork — upstream, the level would be baked into the recording and the
column fader's 0 dB ceiling would make it unrecoverable.

Two things it deliberately does not do.

**It does not compose a level of its own.** While capturing, the target is
`LoopMix.wet_for(loop)` — the exact number the loop will play at, master and
auto-law included. Not a similar number computed here. If the two were
computed separately they would drift, and the symptom would be monitoring that
is subtly wrong against playback, which is the hardest kind of wrong to hear.
`loop_mix` remains the single composition point for loop levels; this module
reads its answer.

**It does not decide when a take ends.** The engine does. A take that is
waiting for a cycle boundary reports `WAIT_STOP`, which is in `CAPTURE_STATES`,
so the monitor holds the track's level until the engine actually closes the
take rather than until the button came up. That answers the tail question by
not asking it: no timer here, no second opinion about quantize boundaries, and
nothing for `looper_timing` to disagree with.
"""

from __future__ import annotations

import errno
import os
import socket
import threading
import time
from dataclasses import dataclass, field

from apc_faders import CC_MAX
from loop_mix import fader_taper, FADER_CEIL_DB, FADER_FLOOR_DB
from sl_loop_states import (
    SL_STATE_INSERTING,
    SL_STATE_MULTIPLYING,
    SL_STATE_OVERDUBBING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_STOP,
)

#: Every state in which the engine is putting your live playing into a track.
#: Overdub, multiply and insert are captures too — you are playing into that
#: track, so it is that track's level you want to hear yourself at.
#:
#: `WAIT_STOP` is here for the tail: the take is still open, waiting for the
#: boundary. `WAIT_START` is not — nothing is being captured yet, and jumping
#: the monitor early would change your level before the take it belongs to.
CAPTURE_STATES = frozenset(
    {
        SL_STATE_RECORDING,
        SL_STATE_WAIT_STOP,
        SL_STATE_OVERDUBBING,
        SL_STATE_MULTIPLYING,
        SL_STATE_INSERTING,
    }
)

DEFAULT_HOST = "127.0.0.1"
#: Must equal CONTROL_PORT_DEFAULT in native/mpe-live-monitor/mpe-live-monitor.c.
#: `tests/test_live_monitor.py` reads the C source and fails if they drift.
DEFAULT_PORT = 9957

#: How long a capture may go unmentioned before we ask the engine about it.
#:
#: **`register_auto_update` delivers on change, not on a timer.** That is canon
#: in `Documents/specs/session-control-plane-spec.md` ("a permanent property to
#: design around") and it was measured again on 2026-09-20 against a real
#: SooperLooper in `tests/engine/`: five seconds inside a held RECORDING take
#: produced one datagram, not fifty.
#:
#: This module's first answer to "a capture that never closes" was a
#: time-to-live, written on the belief that state arrived ten times a second.
#: Against an engine that speaks only on change, that expires a take while it is
#: still recording — measured at +16 dB into the monitor, 2.1 s into a take.
#:
#: So nothing expires on a timer here. A capture held this long without news is
#: *asked about*: the bench sends a `get` whose reply arrives on the same path
#: as any other state update, and the answer — not the silence — decides.
VERIFY_AFTER_S = float(os.environ.get("MPE_LIVE_MONITOR_VERIFY_AFTER_S", "3.0"))

#: How many unanswered questions before the capture is dropped.
#:
#: More than one because the question is a UDP datagram to a process under
#: realtime load: one lost packet, or one stall longer than the timeout, would
#: otherwise end a take that is still recording and hand the monitor back to
#: the live level — measured at +16 dB in the middle of a take. Three strikes
#: costs about four and a half seconds to notice a genuinely absent engine,
#: which nothing is waiting on.
VERIFY_ATTEMPTS = int(os.environ.get("MPE_LIVE_MONITOR_VERIFY_ATTEMPTS", "3"))

#: How long each question may go unanswered before it is asked again.
#: An engine that does not answer is an engine that cannot tell us the take
#: ended, so falling back to the live level is the safe direction: you hear
#: yourself play, rather than being silent in the phones while the instrument
#: reads as dead.
VERIFY_TIMEOUT_S = float(os.environ.get("MPE_LIVE_MONITOR_VERIFY_TIMEOUT_S", "1.5"))

#: Don't re-send a level that has not meaningfully moved. The client ramps, so
#: a repeat is harmless, but the socket write is not free and the log line is
#: noise.
SEND_TOLERANCE = 1e-4

def enabled(value: str | None = None) -> bool:
    """One law for "is the live monitor on", shared with the shell.

    `scripts/start-mpe-live-monitor.sh` lowercases the same variable and matches
    the same four words. They diverged at first — Python treated anything not in
    a deny-list as on, so `MPE_LIVE_MONITOR=off` was off but `OFF`, `no`, `False`
    and `2` were on here and off there. Half a feature running is worse than
    either state, so both sides now answer from this list.
    """
    raw = (value if value is not None else os.environ.get("MPE_LIVE_MONITOR", "0"))
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


ENABLED = enabled()


def resolve_port() -> int:
    raw = os.environ.get("MPE_LIVE_MONITOR_PORT", "")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def resolve_live_cc() -> int:
    """The level you hear yourself at when nothing is capturing, as a CC.

    Unity by default: an enabled monitor with nothing configured must sound
    exactly like no monitor at all.
    """
    raw = os.environ.get("MPE_LIVE_MONITOR_CC", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return CC_MAX
    return max(0, min(CC_MAX, value))


@dataclass
class LiveMonitor:
    """Which track you are playing into, and therefore how loud you hear it."""

    live_gain: int = field(default_factory=resolve_live_cc)
    #: Capturing loops -> [arrival order, last heard, asked at, attempts]. Ordered because
    #: the answer to "which track am I playing into" when two are somehow open
    #: is the one that opened last. `asked at` is None until the bench has put a
    #: question to the engine about a capture that has gone quiet.
    _capturing: dict[int, list] = field(default_factory=dict)
    _seq: int = 0
    #: `sl_osc_session` serves OSC on `ThreadingOSCUDPServer` — a thread per
    #: datagram — so every `state` update arrives on its own thread while the
    #: main MIDI loop reads the same state on a fader move. The unguarded
    #: version had a check-then-act remove that could raise inside an OSC
    #: handler thread, where the traceback goes nowhere.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _clock = staticmethod(time.monotonic)

    def note_state(self, loop: int, state: int, *, now: float | None = None) -> None:
        """Feed an engine `state` update. Order of arrival is what it says."""
        loop = int(loop)
        stamp = self._clock() if now is None else now
        with self._lock:
            if int(state) in CAPTURE_STATES:
                order = self._capturing[loop][0] if loop in self._capturing else self._next_seq()
                # News of any kind clears the outstanding question.
                self._capturing[loop] = [order, stamp, None, 0]
            else:
                self._capturing.pop(loop, None)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def capturing_loop(self, *, now: float | None = None) -> int | None:
        stamp = self._clock() if now is None else now
        with self._lock:
            return self._capturing_loop_locked(stamp)

    def _capturing_loop_locked(self, stamp: float) -> int | None:
        # Only a question that went unanswered drops a capture. Silence from the
        # engine means nothing changed, which is the normal state of a long take.
        unanswered = [
            loop
            for loop, (_order, _seen, asked, attempts) in self._capturing.items()
            if asked is not None
            and attempts >= VERIFY_ATTEMPTS
            and (stamp - asked) > VERIFY_TIMEOUT_S
        ]
        for loop in unanswered:
            self._capturing.pop(loop, None)
        if not self._capturing:
            return None
        return max(self._capturing.items(), key=lambda item: item[1][0])[0]

    def needs_verification(self, *, now: float | None = None) -> int | None:
        """A capture that has gone quiet long enough to be worth asking about.

        Returns the loop to ask about and records that it was asked. The caller
        sends the engine a `get` for that loop's `state`; the reply comes back
        through the ordinary state path and calls `note_state`, which clears the
        question. An unanswered question is re-asked up to VERIFY_ATTEMPTS
        times; only after that does the capture drop.
        """
        stamp = self._clock() if now is None else now
        with self._lock:
            for loop, entry in sorted(
                self._capturing.items(), key=lambda item: item[1][0], reverse=True
            ):
                _order, seen, asked, attempts = entry
                due = asked is None and (stamp - seen) >= VERIFY_AFTER_S
                retry = (
                    asked is not None
                    and attempts < VERIFY_ATTEMPTS
                    and (stamp - asked) > VERIFY_TIMEOUT_S
                )
                if due or retry:
                    entry[2] = stamp
                    entry[3] = attempts + 1
                    return loop
        return None

    def live_amp(self) -> float:
        return fader_taper(
            self.live_gain, floor_db=FADER_FLOOR_DB, ceil_db=FADER_CEIL_DB
        )

    def target_amp(self, wet_for, *, now: float | None = None) -> float:
        """The amplitude the gain stage should be at now.

        `wet_for` is `LoopMix.wet_for` — passed in rather than imported so the
        composed level has exactly one author and this module has no opinion
        about master or auto-law.
        """
        loop = self.capturing_loop(now=now)
        if loop is None:
            return max(0.0, min(1.0, self.live_amp()))
        return max(0.0, min(1.0, float(wet_for(loop))))


#: Seconds between complaints about an unreachable gain stage.
#:
#: Rate-limited by time rather than by a count of refusals, because refusals do
#: not arrive in a clean run: a connected UDP socket reports the ICMP error on
#: the send *after* the one that provoked it, so sends alternate between
#: apparent success and refusal. Counting consecutive refusals put the streak
#: back to 1 on every other datagram, which would have complained every other
#: datagram — the journal flood the counter was there to prevent.
REFUSAL_COMPLAIN_S = float(os.environ.get("MPE_LIVE_MONITOR_REFUSAL_LOG_S", "30"))

#: Deliveries in a row before an unreachable gain stage counts as recovered.
#: More than one for the same reason the complaint is time-limited: with the
#: error arriving one send late, a dead gain stage produces a success between
#: every pair of refusals, and treating one success as recovery announced the
#: monitor fixed several times a second while it was still dead.
RECOVERY_STREAK = int(os.environ.get("MPE_LIVE_MONITOR_RECOVERY_STREAK", "3"))


class LiveMonitorSender:
    """Fire-and-forget amplitudes at the gain stage, deduplicated.

    Never raises and never blocks: the monitor going quiet on a socket error
    would be a worse failure than the level being stale, and this runs in the
    same loop that has to service MIDI on time.

    It does, however, say when nobody is listening. `ECONNREFUSED` is exactly
    what loopback returns when the gain stage is not running, and swallowing it
    made "the monitor is working" and "every datagram has been rejected for an
    hour" produce identical output — nothing. That is this project's own named
    failure shape (AGENTS.md, Rule -1).
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        send=None,
        log=None,
    ) -> None:
        self.host = host
        self.port = resolve_port() if port is None else port
        self._sock: socket.socket | None = None
        self._send = send
        self._log = log
        self._last: float | None = None
        #: Consecutive refusals, and every refusal since the sender opened.
        #: The total is the one a health check should ask about; the streak only
        #: says whether the last datagram landed.
        self.refused = 0
        self.refused_total = 0
        self.delivered = 0
        self._ok_streak = 0
        self._complained_at: float | None = None
        self.error: str | None = None
        #: The main MIDI loop and the OSC handler threads both send.
        self._lock = threading.Lock()

    def open(self) -> bool:
        """Open the socket, *connected* to the gain stage.

        Connected on purpose, though nothing is ever received: an unconnected
        UDP socket silently drops a datagram nobody is listening for, so the
        first version of this could not tell a running gain stage from an absent
        one — it reported zero errors either way. A connected socket gets the
        ICMP port-unreachable back as `ECONNREFUSED` on a following send, which
        is what `refused` counts. The cost is that the *first* datagram after
        the gain stage dies still looks delivered.
        """
        if self._send is not None:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.connect((self.host, self.port))
            self._sock = sock
        except OSError as exc:
            self.error = f"{exc}"
            self._sock = None
            return False
        return True

    def send(self, amp: float) -> bool:
        """Send `amp` if it has moved. True when something went out.

        The dedup check and the send are one critical section: two threads that
        both passed the tolerance check could otherwise reach `sendto` in either
        order and leave the gain stage on the older of two levels — audible,
        sticky, and indistinguishable from a hardware fault.
        """
        value = max(0.0, min(1.0, float(amp)))
        with self._lock:
            if self._last is not None and abs(value - self._last) <= SEND_TOLERANCE:
                return False
            payload = f"gain {value:.6f}".encode("ascii")
            if self._send is not None:
                self._send(payload)
                self._last = value
                self.delivered += 1
                self.refused = 0
                self._ok_streak += 1
                return True
            if self._sock is None:
                return False
            try:
                self._sock.send(payload)
            except OSError as exc:
                self._note_refusal(exc)
                return False
            self._last = value
            self.delivered += 1
            self.refused = 0
            self._ok_streak += 1
            if self._complained_at is not None and self._ok_streak >= RECOVERY_STREAK:
                self._say(
                    f"live monitor reachable again ({self.refused_total} level "
                    "update(s) were refused)"
                )
                self._complained_at = None
                self.refused_total = 0
            self.error = None
            return True

    def _note_refusal(self, exc: OSError) -> None:
        self.refused += 1
        self.refused_total += 1
        self._ok_streak = 0
        self.error = f"{exc}"
        now = time.monotonic()
        if (
            self._complained_at is None
            or (now - self._complained_at) >= REFUSAL_COMPLAIN_S
        ):
            self._complained_at = now
            self._say(
                f"live monitor unreachable on {self.host}:{self.port} ({exc}) — "
                f"{self.refused_total} level update(s) refused; "
                "is mpe-live-monitor.service running?"
            )

    def _say(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
