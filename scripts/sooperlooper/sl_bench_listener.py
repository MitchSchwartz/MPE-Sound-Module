"""OSC state auto-update routing for APC footswitch bench (criterion 41)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apc_footswitch import LoopFootswitch



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
        self._num_loops = 16

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if control == "wet":
            if self._on_wet is not None:
                self._on_wet(int(loop_index), float(value))
            return
        fs = self._by_loop.get(loop_index)
        if fs is None:
            return
        if control == "state":
            fs.sync_from_sl(int(value))
        elif control == "loop_len":
            fs.sync_loop_len(float(value))
        elif control == "loop_pos":
            fs.sync_loop_pos(float(value))

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
