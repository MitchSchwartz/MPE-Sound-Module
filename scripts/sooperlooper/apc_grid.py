"""APC mini 8×8 grid ↔ SooperLooper loop indices (eval + future product).

Layout (Mitch 2026-08-16) — **Ableton-style: tracks run left to right.**

The 16 loops are one horizontal line of tracks, not two stacked rows. The APC
is eight columns wide, so it is a *viewport* onto that line: eight tracks
visible at a time, banked with the arrow buttons.

  row 0 (bottom) → the eight visible tracks, offset .. offset+7
  rows 1–7       → reserved (per-track controllers, scene rows — future)

This replaces the earlier row-0/row-3 split, where fader N drove loops N *and*
N+8 because both lived in the same grid column. Under the viewport a column
holds exactly one track, so a fader means one track — and which track it means
changes when the bank moves. That is the point: it is what makes "one clip at a
time per column" and per-column patch state expressible later, and neither is
expressible while two unrelated loops share a column.

**The view is a value, not a global.** `GridView` is frozen and `scrolled()`
returns a new one. Exactly one owner (the bench event loop) holds the current
view and passes it to the LED painter, the note handler and the fader layer,
so those three cannot drift into disagreeing about which track is where.
"""

from __future__ import annotations

from dataclasses import dataclass

from sl_limits import MAX_USABLE_LOOPS

# 15, not 16 — SooperLooper 1.7.9 stops at index 14. See sl_limits.
NUM_LOOPS = MAX_USABLE_LOOPS
GRID_COLS = 8
GRID_ROWS = 8

# Rows are slots: eight per track, row 0 at the bottom (`pad_note` convention).
NUM_SLOTS = GRID_ROWS

# The row addressed by the single-clip API (`loop_for_pad`, `all_clip_pads`).
# Kept because unconverted callers mean "the track's clip" by it.
CLIP_ROW = 0
LOOP_CLIP_ROWS: tuple[int, ...] = (CLIP_ROW,)
CONTROLLER_ROWS: tuple[int, ...] = tuple(r for r in range(GRID_ROWS) if r != CLIP_ROW)

# How far the viewport can travel: 16 tracks in an 8-wide window.
MAX_VIEW_OFFSET = NUM_LOOPS - GRID_COLS

# Arrow buttons page by a whole screen; Shift+left/right nudge by one track.
PAGE_STEP = GRID_COLS
NUDGE_STEP = 1


def pad_note(row: int, col: int) -> int:
    """APC mini grid note: row 0 = bottom, col 0 = left. Notes 0–63."""
    if not 0 <= row <= 7 or not 0 <= col <= 7:
        raise ValueError(f"row/col out of range: ({row}, {col})")
    return row * 8 + col


# Grid notes for rows 1–7 (reserved — multi-clip slot rows 1–7 land here in P3).
RESERVED_GRID_NOTES: tuple[int, ...] = tuple(
    pad_note(row, col) for row in range(1, GRID_ROWS) for col in range(GRID_COLS)
)


def note_to_row_col(note: int) -> tuple[int, int] | None:
    if not 0 <= note <= 63:
        return None
    return note // 8, note % 8


def is_clip_note(note: int) -> bool:
    """Is this note a clip pad at all (any bank)?"""
    rc = note_to_row_col(note)
    return rc is not None and rc[0] == CLIP_ROW


def is_reserved_grid_note(note: int) -> bool:
    """Grid rows 1–7 — not wired until multi-clip P3."""
    rc = note_to_row_col(note)
    return rc is not None and rc[0] != CLIP_ROW


@dataclass(frozen=True)
class GridView:
    """Which eight tracks the surface is showing, and where each one sits.

    `offset` is the leftmost visible track. Clamped to [0, MAX_VIEW_OFFSET] —
    never wrapped. A wrap teleports the bank mid-jam and the surface gives no
    warning that it happened; running off the end and stopping is the one
    behaviour a player can feel without looking.
    """

    offset: int = 0
    num_loops: int = NUM_LOOPS

    def __post_init__(self) -> None:
        max_offset = max(0, self.num_loops - GRID_COLS)
        clamped = max(0, min(max_offset, int(self.offset)))
        object.__setattr__(self, "offset", clamped)

    # -- travel -----------------------------------------------------------

    @property
    def max_offset(self) -> int:
        return max(0, self.num_loops - GRID_COLS)

    def scrolled(self, delta: int) -> "GridView":
        """New view moved by `delta` tracks, clamped at both ends."""
        return GridView(offset=self.offset + int(delta), num_loops=self.num_loops)

    def can_scroll(self, delta: int) -> bool:
        return self.scrolled(delta).offset != self.offset

    # -- pads -------------------------------------------------------------

    def loop_for_pad(self, row: int, col: int) -> int | None:
        """Track under this pad in this bank, or None if it is not a clip pad."""
        if row != CLIP_ROW or not 0 <= col <= 7:
            return None
        loop = self.offset + col
        return loop if loop < self.num_loops else None

    def loop_for_note(self, note: int) -> int | None:
        rc = note_to_row_col(note)
        if rc is None:
            return None
        return self.loop_for_pad(rc[0], rc[1])

    def note_for_loop(self, loop: int) -> int | None:
        """Pad note showing this track, or None while it is banked off-screen."""
        col = loop - self.offset
        if not 0 <= col < GRID_COLS:
            return None
        return pad_note(CLIP_ROW, col)

    def visible_loops(self) -> tuple[int, ...]:
        return tuple(
            loop
            for loop in range(self.offset, self.offset + GRID_COLS)
            if loop < self.num_loops
        )

    def visible_pads(self) -> list[tuple[int, int, int]]:
        """(row, col, loop_index) for every clip pad in this bank."""
        return [
            (CLIP_ROW, col, loop)
            for col in range(GRID_COLS)
            if (loop := self.loop_for_pad(CLIP_ROW, col)) is not None
        ]


    # -- cells (multi-clip): every row is a slot ---------------------------
    #
    # The clip-row methods above address one clip per track. These address the
    # full matrix: a **column is a track**, a **row is a slot**, and one slot
    # per track is audible at a time — Ableton Session View semantics.
    #
    # Both sets coexist on purpose. `loop_for_pad` keeps meaning "the clip row",
    # so a caller that has not been converted keeps its old behaviour instead of
    # silently acquiring seven new rows of pads it does not handle.

    def track_for_pad(self, row: int, col: int) -> int | None:
        """Track under this pad. Depends on the column only — the row is the
        slot, and banking moves tracks, never slots."""
        if not 0 <= row < GRID_ROWS or not 0 <= col < GRID_COLS:
            return None
        track = self.offset + col
        return track if track < self.num_loops else None

    def cell_for_note(self, note: int) -> tuple[int, int] | None:
        """(track, slot) under this pad note in this bank, or None."""
        rc = note_to_row_col(note)
        if rc is None:
            return None
        row, col = rc
        track = self.track_for_pad(row, col)
        return None if track is None else (track, row)

    def note_for_cell(self, track: int, slot: int) -> int | None:
        """Pad note for a cell, or None while its track is banked off-screen."""
        if not 0 <= slot < NUM_SLOTS:
            return None
        col = track - self.offset
        if not 0 <= col < GRID_COLS:
            return None
        return pad_note(slot, col)

    def visible_cells(self) -> list[tuple[int, int, int]]:
        """(row, col, track) for every pad in the matrix under this bank.

        Filters rather than assuming a full grid. With 15 tracks and
        MAX_VIEW_OFFSET = 7 every bank happens to be full — the last page
        overlaps the first by one track instead of leaving a gap — but that is
        arithmetic that holds only while NUM_LOOPS - GRID_COLS is the clamp.
        A future track count that does not divide cleanly would leave empty
        columns, and a grid painted as if they were tracks would light pads
        that address nothing.
        """
        return [
            (row, col, track)
            for row in range(GRID_ROWS)
            for col in range(GRID_COLS)
            if (track := self.track_for_pad(row, col)) is not None
        ]

    def loops_for_column(self, col: int) -> tuple[int, ...]:
        """Tracks sharing grid column `col` — one, under this layout.

        Derived from visible_pads() rather than computed independently, so the
        fader layer follows the pad layout by construction. Change the layout
        and the faders move with the pads instead of quietly disagreeing.
        """
        if not 0 <= col <= 7:
            raise ValueError(f"column out of range: {col}")
        return tuple(loop for _row, c, loop in self.visible_pads() if c == col)


# Default view for callers that have not banked (and for LED-clearing sweeps
# that need every pad the clip row can ever occupy).
DEFAULT_VIEW = GridView()


def all_grid_pads() -> list[tuple[int, int]]:
    """(row, col) for every pad in the 8x8 matrix — bank-independent.

    The multi-clip counterpart of `all_clip_pads`. Clearing must cover every
    row, not just the clip row: after a bank change a pad left lit from the
    previous bank is the one visible symptom of forgetting to clear.
    """
    return [(row, col) for row in range(GRID_ROWS) for col in range(GRID_COLS)]


def all_clip_pads() -> list[tuple[int, int]]:
    """(row, col) for every pad on the clip row — bank-independent.

    Used when clearing: after a bank change the whole row is repainted, and
    a pad left lit from the previous bank is the one visible symptom of
    forgetting to clear first.
    """
    return [(CLIP_ROW, col) for col in range(GRID_COLS)]
