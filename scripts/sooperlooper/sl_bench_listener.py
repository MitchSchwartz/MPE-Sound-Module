"""OSC state auto-update routing for APC gesture bench (criterion 41)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from track_gesture import TrackGesture
from sl_limits import MAX_USABLE_LOOPS



def _noop(*_a, **_k) -> None:
    return None


class SlBenchStateListener:
    """Routes bench auto-update callbacks — no server of its own."""

    def __init__(
        self,
        by_loop: dict[int, TrackGesture],
        on_wet=None,
        *,
        session=None,
    ) -> None:
        self._by_loop = by_loop
        self._on_wet = on_wet
        self._session = session
        self._num_loops = MAX_USABLE_LOOPS
        #: Optional multi-clip surface. Gets every `state` update so it can tell
        #: when a queued switch has actually happened — see SlotSurface.
        self._surface = None

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if control == "wet":
            if self._on_wet is not None:
                self._on_wet(int(loop_index), float(value))
            return
        if control == "in_peak_meter":
            # Routed BEFORE the `_by_loop` lookup, deliberately. The last time
            # this existed the lookup ran first and returned on None, so every
            # tail peak died here: `saw_loud` never set, and the ring-out was
            # cut at a fixed window regardless of how the note actually decayed
            # (PI5-LOOPER-SEAM-WRAP.md, corrected 2026-08-26). Peaks reach the
            # gesture whether or not that loop currently has a pad bound.
            target = self._by_loop.get(loop_index)
            if target is not None:
                target.sync_in_peak(float(value))
            return
        fs = self._by_loop.get(loop_index)
        if fs is None:
            if control == "state" and self._surface is not None:
                self._surface.on_state(int(loop_index), int(value))
            elif control == "loop_len" and self._surface is not None:
                self._surface.on_loop_len(int(loop_index), float(value))
            return
        if control == "state":
            fs.sync_from_sl(int(value))
            if self._surface is not None:
                self._surface.on_state(int(loop_index), int(value))
        elif control == "loop_len":
            fs.sync_loop_len(float(value))
            if self._surface is not None:
                self._surface.on_loop_len(int(loop_index), float(value))
        elif control == "loop_pos":
            fs.sync_loop_pos(float(value))

    def attach_surface(self, surface) -> None:
        """Route state updates to the multi-clip surface as well."""
        self._surface = surface

    def register(self, _client, *, num_loops: int) -> None:
        """Register bench subscriptions on the shared session."""
        if self._session is None:
            raise RuntimeError("SlBenchStateListener requires a shared SlOscSession")
        self._num_loops = num_loops
        self._session.attach_bench_listener(self)
        self._session.register_bench(num_loops=num_loops)

    def maybe_reregister(self) -> None:
        if self._session is not None:
            self._session.maybe_reregister()

    def start(self) -> None:
        """No-op — the shared SlOscSession owns the listen port."""
