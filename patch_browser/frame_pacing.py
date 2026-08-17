"""Draw-loop frame pacing for the touch UI.

Kept out of ``touch_browser_app`` so it is importable (and testable) without pygame.

Why this is an audio module, not a power-saving one: the touch browser hosts a JACK
client (``surge_peak_monitor``), and jackd's realtime graph cycle waits on that
client's callback every period. The callback needs the GIL. Measured on the appliance
2026-08-17, the draw loop redrew unconditionally at 60 fps and held a full core
(101 jiffies/s while idle), so the callback had to wait out a GIL handoff — default
switch interval 5 ms — before it could even start. At a 1024-frame period (21.3 ms)
that survives; at 512 (10.7 ms) it is marginal; at 256 (5.3 ms) it cannot work.

The only always-live elements on screen are the CPU and OUT meters, which sample at
5 Hz, so 60 fps while idle was ~12x more redraws than the content justified.
"""

from __future__ import annotations

ACTIVE_FPS = 60
IDLE_FPS = 20
# Hold the fast rate briefly after the last activity so the tail of a momentum fling
# does not visibly step down mid-glide.
ACTIVE_HOLD_S = 0.35


def frame_rate_for(*, busy: bool, now: float, last_active_at: float) -> tuple[int, float]:
    """Return ``(fps, new_last_active_at)`` for this frame.

    *busy* means the frame did something a viewer would see move: handled an input
    event, or advanced an animation that reported a position change.
    """
    if busy:
        last_active_at = now
    if now - last_active_at < ACTIVE_HOLD_S:
        return ACTIVE_FPS, last_active_at
    return IDLE_FPS, last_active_at
