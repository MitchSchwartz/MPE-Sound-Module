"""OSC state auto-update routing for APC footswitch bench (criterion 41)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apc_footswitch import LoopFootswitch
from sl_limits import MAX_USABLE_LOOPS



def _noop(*_a, **_k) -> None:
    return None


class SlBenchStateListener:
    """Routes bench auto-update callbacks — no server of its own."""

    def __init__(
        self,
        by_loop: dict[int, LoopFootswitch],
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
        fs = self._by_loop.get(loop_index)
        if fs is None:
            # Still forward state: the matrix keeps a slot model for every
            # track, including any without a footswitch bound to a pad.
            if control == "state" and self._surface is not None:
                self._surface.on_state(int(loop_index), int(value))
            return
        if control == "state":
            fs.sync_from_sl(int(value))
            if self._surface is not None:
                self._surface.on_state(int(loop_index), int(value))
        elif control == "loop_len":
            fs.sync_loop_len(float(value))
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
