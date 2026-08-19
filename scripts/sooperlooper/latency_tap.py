"""Pad-down -> next ``/hit`` OSC timing for criterion 42.

Lives in its own module so tests can import it without executing the bench, which
binds MIDI and mutates shared footswitch state on import.
"""

from __future__ import annotations

import time


class LatencyTapClient:
    """Wraps the OSC client so every send is seen, whoever makes it.

    The footswitches hold the client directly — ``build_footswitches(osc=...)`` hands
    it to each one — and the bench's ``_send`` helper goes through it too, so there is
    exactly one pairing point rather than two that can disagree.

    This exists because the first cut hooked ``_send`` instead. Pads never touch
    ``_send``, so the instrument measured nothing and exited without a number:
    267 pad presses, zero samples, on the appliance 2026-08-19.

    A pad-down routed to the fader/mute layer emits no ``/hit`` and leaves an orphan
    timestamp, which the next ``/hit`` pairs with and inflates. Diagnostic only — read
    the percentiles, not a single max.
    """

    def __init__(self, inner, pending: list[float], out: list[float]) -> None:
        self._inner = inner
        self._pending = pending
        self._out = out

    def send_message(self, path: str, args) -> None:
        if self._pending and "/hit" in path:
            self._out.append((time.monotonic() - self._pending.pop(0)) * 1000.0)
        self._inner.send_message(path, args)

    def __getattr__(self, name):
        return getattr(self._inner, name)
