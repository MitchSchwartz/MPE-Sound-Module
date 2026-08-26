"""OSC state auto-update routing for APC footswitch bench (criterion 41)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apc_footswitch import LoopFootswitch

from sl_grid_sync import TAIL_CAPTURE_ENABLED, TAIL_PEAK_UPDATE_MS
from sl_seam_weld import SCRATCH_LOOP, SEAM_WELD_ENABLED


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
        self._tail_peak_loop: int | None = None
        self._tail_peak_owner: int | None = None

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if control == "wet":
            if self._on_wet is not None:
                self._on_wet(int(loop_index), float(value))
            return
        if control == "in_peak_meter":
            # Routed BEFORE the _by_loop lookup: during seam weld the meter is
            # registered on the scratch loop (14), which has no footswitch —
            # the lookup below returns None and would drop every tail peak.
            # With them dropped, _tail_saw_loud never sets and poll_tail_capture
            # falls through to the fixed TAIL_MAX_S cut, so the tail was always
            # truncated at 750 ms instead of ending when the note decayed.
            if loop_index != self._tail_peak_loop:
                return
            owner = self._tail_peak_owner
            if owner is None:
                return
            owner_fs = self._by_loop.get(owner)
            if owner_fs is not None:
                owner_fs.sync_in_peak(float(value))
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

    def register_tail_peak(self, owner_loop: int) -> None:
        if not TAIL_CAPTURE_ENABLED or self._session is None:
            return
        if self._tail_peak_loop is not None:
            self.unregister_tail_peak()
        meter_loop = SCRATCH_LOOP if SEAM_WELD_ENABLED else owner_loop
        self._tail_peak_owner = owner_loop
        self._tail_peak_loop = meter_loop
        self._session.register_tail_peak(meter_loop, update_ms=TAIL_PEAK_UPDATE_MS)

    def unregister_tail_peak(self, _loop: int | None = None) -> None:
        if self._session is None or self._tail_peak_loop is None:
            self._tail_peak_loop = None
            self._tail_peak_owner = None
            return
        loop = self._tail_peak_loop
        self._tail_peak_loop = None
        self._tail_peak_owner = None
        self._session.unregister_tail_peak(loop)

    def wire_tail_capture(self, footswitches: list[LoopFootswitch]) -> None:
        for fs in footswitches:
            fs.set_tail_capture_hooks(
                self.register_tail_peak,
                self.unregister_tail_peak,
            )

    def maybe_reregister(self) -> None:
        if self._session is not None:
            self._session.maybe_reregister()

    def start(self) -> None:
        """No-op — the shared SlOscSession owns the listen port."""
