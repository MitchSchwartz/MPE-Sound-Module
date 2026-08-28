"""Keep the APC attached, and notice when it is not.

Measured on the appliance 2026-08-27, after three "it's still dead" rounds
where every reading I had said the session was healthy:

    usb 1-1.1.3: urb status -32        (x3168)
    usb 1-1.1.3: USB disconnect, device number 23
    usb 1-1.1.3: new full-speed USB device number 24

-32 is -EPIPE: the APC MINI stalls its bulk endpoint and drops off the bus.
Every disconnect timestamp lined up with a session start, and the device
number had climbed to 24 in one morning. Four starts in six left the pads
dead.

Two causes, both addressed here.

WHY IT STALLS. The APC is a 12 Mbit full-speed device two hubs deep, sharing
that chain with a Scarlett 4i4 streaming audio. Startup blanks all 64 pads and
the multigrid's first paint writes 64 more, back to back, as fast as Python can
call `send_message`. That burst into a contended full-speed chain is what
stalls the endpoint. `PacedMidiOut` spreads writes so the burst cannot happen;
the steady-state diffing repaint was never the problem.

WHY IT STAYED DEAD. When the device re-enumerates, our ALSA client survives —
subscribed to a device that no longer exists. `open_port` reported success once
and nothing ever asked again, so the bench ran for hours against a dead input
while printing a complete, correct banner. That is the appliance's recurring
defect shape: a reading identical whether it is working or broken. `LinkHealth`
re-asks the kernel on a timer and reopens, loudly.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable

from midi_subscription import port_subscriptions

#: Minimum gap between MIDI writes to the APC. At 31.25 kbaud a 3-byte message
#: is ~1 ms of wire time, so anything faster only fills buffers. Measured: 64
#: pads at this rate is ~96 ms, invisible to a player, and no stall.
DEFAULT_GAP_S = 0.0015

#: How often to re-ask the kernel whether we still have the device.
DEFAULT_CHECK_S = 2.0


class PacedMidiOut:
    """A `send_message` that never bursts.

    Drop-in for an rtmidi MidiOut. Messages queue and leave one per `gap_s`,
    drained by `pump()` from the caller's existing idle loop. Nothing here
    sleeps: this runs on the same thread that handles pad presses, and a
    blocking pacer would trade dead pads for late ones.
    """

    def __init__(self, midi_out, *, gap_s: float = DEFAULT_GAP_S,
                 now: Callable[[], float] = time.monotonic) -> None:
        self._out = midi_out
        self._gap_s = float(gap_s)
        self._now = now
        self._queue: deque[list[int]] = deque()
        self._next_at = 0.0

    @property
    def backlog(self) -> int:
        return len(self._queue)

    def send_message(self, msg) -> None:
        self._queue.append(list(msg))

    def pump(self) -> int:
        """Send whatever the pacing budget allows. Returns messages sent."""
        sent = 0
        while self._queue:
            now = self._now()
            if now < self._next_at:
                break
            try:
                self._out.send_message(self._queue.popleft())
            except Exception:
                # A write during re-enumeration raises. Drop the backlog: it
                # describes a surface that no longer exists, and LinkHealth is
                # about to force a full repaint anyway.
                self._queue.clear()
                return sent
            self._next_at = now + self._gap_s
            sent += 1
        return sent

    def drain(self, *, timeout_s: float = 2.0) -> None:
        """Block until the queue empties. Startup only, never the play loop."""
        deadline = self._now() + timeout_s
        while self._queue and self._now() < deadline:
            if self.pump() == 0:
                time.sleep(self._gap_s)

    def reset(self, midi_out=None) -> None:
        """Point at a reopened port and discard the stale backlog."""
        if midi_out is not None:
            self._out = midi_out
        self._queue.clear()
        self._next_at = 0.0


class LinkHealth:
    """Re-ask the kernel whether we still have the device, and reopen if not.

    `on_lost` must reopen the ports and repaint; it returns True if it managed
    to. Until it does, this keeps trying — a device that comes back thirty
    seconds later must bring the pads back with it, without Mitch restarting
    anything.
    """

    def __init__(self, device_key: str, *, on_lost: Callable[[], bool],
                 log: Callable[[str], None],
                 check_s: float = DEFAULT_CHECK_S,
                 now: Callable[[], float] = time.monotonic) -> None:
        self._key = device_key
        self._on_lost = on_lost
        self._log = log
        self._check_s = float(check_s)
        self._now = now
        self._next_check = 0.0
        self._healthy = True
        self._losses = 0

    @property
    def losses(self) -> int:
        return self._losses

    @property
    def healthy(self) -> bool:
        return self._healthy

    def poll(self) -> None:
        now = self._now()
        if now < self._next_check:
            return
        self._next_check = now + self._check_s
        has_reader, _has_writer = port_subscriptions(self._key)
        if has_reader:
            if not self._healthy:
                self._log(
                    f"APC link RESTORED after {self._losses} loss(es) — pads live again"
                )
            self._healthy = True
            return
        if self._healthy:
            self._losses += 1
            self._log(
                f"APC link LOST — {self._key!r} has no reader in "
                f"/proc/asound/seq/clients. The device re-enumerated (USB "
                f"endpoint stall); our client is subscribed to a device that "
                f"no longer exists. Pads are dead until reopened. Reopening..."
            )
        self._healthy = False
        if self._on_lost():
            # Confirm rather than assume: the whole bug was trusting that an
            # open which returned success had actually subscribed.
            has_reader, _ = port_subscriptions(self._key)
            if has_reader:
                self._healthy = True
                self._log("APC link reopened — pads live again")
            else:
                self._log("APC reopen returned success but still no reader — retrying")
