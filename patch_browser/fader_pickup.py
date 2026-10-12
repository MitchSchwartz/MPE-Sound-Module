"""Pickup for controls without motors: where each one was last seen, and the
one law that turns its moves into a value.

A hardware fader or knob sends nothing until it moves, and when what it
controls changes under it (a bank change, a patch load, a target switch, a
move made on the touch screen) its physical position no longer says anything
about the value in force. Applying its next message at face value jumps the
value. Two consumers share this module:

* `scripts/sooperlooper/loop_mix.py` (APC column faders);
* `patch_browser/fx_policy.py` (`Cc1Router`, the mod knob).

**The law is scaling (relative) pickup** (`scaled`). Each move changes the
value by the fraction of the remaining physical travel it covered, applied to
the remaining travel of the value, toward the end the control is heading for.
So a move responds at once, in the direction moved, and never jumps; and at
either end of travel the value meets the control (bottom is 0, top is full).
The value is only ever the value in force plus moves, so when that value moves
under the control (the "re-arm" points above), the next move simply starts
from the new value.

`Pickup` keeps one anchor per control: the last raw value it sent. A control
never seen has no anchor, so its first message only anchors and changes
nothing. No I/O, no clock.
"""

from __future__ import annotations

import os

#: Pickup tolerance in CC steps. Faders are noisy near the detent. Used by
#: loop_mix to tell an engine echo from a foreign write (the env name predates
#: the mod knob).
PICKUP_TOLERANCE_CC = int(os.environ.get("MPE_APC_FADER_PICKUP_CC", "2"))

#: Full travel of a 7-bit control.
CC_MAX = 127


def scaled(prev: int, raw: int, ref: float, top: int = CC_MAX) -> float:
    """The value after the control moved from `prev` to `raw`, from `ref`.

    All three in the control's units (0..`top`); the result is a float so slow
    moves accumulate instead of rounding away. A control at 10 driving a value
    at `top` halves it by moving to 5 and reaches 0 at 0.
    """
    if raw < prev:
        level = ref - ref * (prev - raw) / prev
    elif raw > prev:
        level = ref + (top - ref) * (raw - prev) / (top - prev)
    else:
        level = ref
    return max(0.0, min(float(top), level))


class Pickup:
    """Per-control anchors: the last raw value each control sent."""

    def __init__(self) -> None:
        self._anchor: dict[object, int] = {}

    def __contains__(self, key: object) -> bool:
        return key in self._anchor

    def rearm(self, key: object | None = None) -> None:
        """Forget where the control is: its next touch only anchors.

        For a control whose position is no longer known (loop_mix: a bank
        change rebinds every fader). No key forgets every control.
        """
        if key is None:
            self._anchor.clear()
            return
        self._anchor.pop(key, None)

    def first_touch(self, key: object, raw: int) -> bool:
        """True when this message only anchored the control (its first touch)."""
        if key in self._anchor:
            return False
        self._anchor[key] = int(raw)
        return True

    def previous(self, key: object) -> int:
        """Where the control was last seen. Only valid after `first_touch`."""
        return self._anchor[key]

    def moved(self, key: object, raw: int) -> None:
        self._anchor[key] = int(raw)

    def follow(self, key: object, raw: int, ref: float) -> float | None:
        """The scaled value for this message from `ref`, or None when it only
        anchored the control. Records where the control is either way."""
        raw = int(raw)
        if self.first_touch(key, raw):
            return None
        value = scaled(self._anchor[key], raw, ref)
        self._anchor[key] = raw
        return value
