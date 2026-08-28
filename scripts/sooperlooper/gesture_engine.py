"""Shared record/close gestures — single-clip footswitch and multigrid matrix.

Wraps ``loop_model.plan_gesture`` so both control paths send the same OSC
sequence for arming and closing a take (ring-out, quantize stop, grid arm).
"""

from __future__ import annotations

from dataclasses import dataclass

from loop_model import plan_gesture
from sl_grid_sync import RING_OUT_ENABLED
from sl_loop_states import SL_STATE_OFF


@dataclass(frozen=True)
class GesturePlan:
    commands: tuple[str, ...] = ()
    arm_grid: bool = False
    queue_stop: bool = False
    note: str = ""


def _from_loop_plan(plan) -> GesturePlan:
    return GesturePlan(
        commands=tuple(plan.commands),
        arm_grid=plan.arm_grid,
        queue_stop=plan.queue_stop,
        note=plan.note or "",
    )


def plan_arm_record(
    *,
    grid_established: bool,
    is_defining: bool,
    quantized: bool,
) -> GesturePlan:
    """Pad down on an empty cell — arm record into this slot."""
    return _from_loop_plan(
        plan_gesture(
            edge="down",
            sl_state=SL_STATE_OFF,
            pending=None,
            grid_established=grid_established,
            is_defining=is_defining,
            quantized=quantized,
            tail_capture_enabled=RING_OUT_ENABLED,
        )
    )


def plan_close_take(
    *,
    sl_state: int,
    grid_established: bool,
    is_defining: bool,
    quantized: bool,
) -> GesturePlan:
    """Pad down while recording this slot — close the take."""
    return _from_loop_plan(
        plan_gesture(
            edge="down",
            sl_state=sl_state,
            pending=None,
            grid_established=grid_established,
            is_defining=is_defining,
            quantized=quantized,
            tail_capture_enabled=RING_OUT_ENABLED,
        )
    )
