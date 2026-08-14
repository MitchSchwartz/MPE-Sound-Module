"""APC mini 8×8 grid ↔ SooperLooper loop indices (eval + future product).

Layout (Mitch 2026-08-14):
  row 0 (bottom) → loops 0–7
  row 3          → loops 8–15
  rows 1, 2, 4–7 → reserved for per-loop controllers (future)
"""

from __future__ import annotations

LOOP_CLIP_ROWS: tuple[int, ...] = (0, 3)
CONTROLLER_ROWS: tuple[int, ...] = (1, 2, 4, 5, 6, 7)
NUM_LOOPS = 16


def pad_note(row: int, col: int) -> int:
    """APC mini grid note: row 0 = bottom, col 0 = left. Notes 0–63."""
    if not 0 <= row <= 7 or not 0 <= col <= 7:
        raise ValueError(f"row/col out of range: ({row}, {col})")
    return row * 8 + col


def note_to_row_col(note: int) -> tuple[int, int] | None:
    if not 0 <= note <= 63:
        return None
    return note // 8, note % 8


def loop_index_for_pad(row: int, col: int) -> int | None:
    if row == 0:
        return col
    if row == 3:
        return 8 + col
    return None


def loop_index_for_note(note: int) -> int | None:
    rc = note_to_row_col(note)
    if rc is None:
        return None
    return loop_index_for_pad(rc[0], rc[1])


def all_loop_pads() -> list[tuple[int, int, int]]:
    """(row, col, loop_index) for all 16 clip pads."""
    out: list[tuple[int, int, int]] = []
    for col in range(8):
        out.append((0, col, col))
    for col in range(8):
        out.append((3, col, 8 + col))
    return out
