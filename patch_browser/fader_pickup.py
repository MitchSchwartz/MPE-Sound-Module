"""Pickup for controls without motors: where each one was last seen.

A hardware fader or knob sends nothing until it moves, and when what it
controls changes under it (a bank change, a patch load, a target switch, a
move made on the touch screen) its physical position no longer says anything
about the value in force. Applying its next message at face value jumps the
value. Two consumers share the bookkeeping here:

* `scripts/sooperlooper/loop_mix.py` (APC column faders): relative pickup.
  The first touch anchors and changes nothing; every move after that is
  scaled from the anchor (`LoopMix._scaled_level`).
* `patch_browser/fx_policy.py` (`Cc1Router`, the mod knob): crossing pickup.
  The knob changes nothing until it reaches or crosses the value in force,
  then follows it (`Pickup.catch`).

Both keep one anchor per control, start from "first touch only anchors", and
re-arm by dropping the anchor. That part lives here, once; each law reads it.
No I/O, no clock.
"""

from __future__ import annotations

import os

#: Pickup tolerance in CC steps. Faders are noisy near the detent; requiring
#: an exact crossing can leave a control permanently inert. One setting for
#: every surface (the env name predates the mod knob).
PICKUP_TOLERANCE_CC = int(os.environ.get("MPE_APC_FADER_PICKUP_CC", "2"))


class Pickup:
    """Per-control anchors (the last raw value each control sent), plus which
    controls have caught the value they drive."""

    def __init__(self) -> None:
        self._anchor: dict[object, int] = {}
        self._caught: set[object] = set()

    def __contains__(self, key: object) -> bool:
        return key in self._anchor

    def rearm(self, key: object | None = None) -> None:
        """What the control drives moved without it: the next touch re-anchors.

        No key re-arms every control (a bank change rebinds them all).
        """
        if key is None:
            self._anchor.clear()
            self._caught.clear()
            return
        self._anchor.pop(key, None)
        self._caught.discard(key)

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

    def catch(
        self,
        key: object,
        raw: int,
        value: float,
        *,
        tolerance: float = PICKUP_TOLERANCE_CC,
    ) -> bool:
        """Crossing pickup: may this message set `value` (in the same CC units)?

        Not until the control reaches the value (within `tolerance`) or moves
        across it between two messages, from either side; from then on, every
        message until the next `rearm`. Always records where the control is.
        """
        raw = int(raw)
        if key in self._caught:
            self._anchor[key] = raw
            return True
        prev = self._anchor.get(key)
        self._anchor[key] = raw
        reached = abs(raw - value) <= tolerance
        crossed = prev is not None and min(prev, raw) <= value <= max(prev, raw)
        if reached or crossed:
            self._caught.add(key)
            return True
        return False

    def caught(self, key: object) -> bool:
        return key in self._caught
