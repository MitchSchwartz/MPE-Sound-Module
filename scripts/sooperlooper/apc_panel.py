"""THE canonical APC mini panel map. One source of truth for every control.

Why this file exists, in Mitch's words on 2026-08-27: "I feel like we need a
canonical button map to never fuck this up again."

He was right, and the history earns the file. The scene-row mapping was wrong
in production, I "fixed" it in the wrong direction from a docstring that
contradicted the note table three lines above it, then shipped it off by one
in the other direction. Three wrong answers to a question the hardware answers
unambiguously. The cause was never difficulty — it was that the panel had no
single description, so each module carried its own half-remembered version and
the prose drifted away from the constants.

RULES FOR THIS FILE
  1. Every fact here is measured on the device or read off the panel, and says
     which. Nothing is inferred from another constant's name.
  2. No other module defines a note number. They import from here.
  3. If a fact is disputed, it gets measured again — not reasoned about. The
     three wrong answers were all produced by reasoning.

WHERE THE NUMBERS LIVE NOW — 2026-08-30

Rule 2 was right and unenforceable: by 2026-08-30 seven constants outside this
file named nineteen notes, and one of them — `apc_transport.ARROW_NOTES_MK2` —
had been sitting inside `SCENE_COLUMN_MK2` for weeks, killing banking on the
attached mk2 with 126 green tests over it. A rule in a docstring cannot fail a
build.

So the note numbers moved down one layer, to `control_registry.py`, where
every control is one row and a test walks the AST of every APC module to make
sure no literal escapes. The names below are unchanged and mean exactly what
they always did; they are now views onto the registry rather than the place the
digits are typed. This file keeps what it is actually for: the panel drawing,
the vertical flip, and the pure functions that stop call sites re-deriving it.

The drawing below is a picture, not a source — the registry is the source.

THE PANEL (APC mini mk1)

    ┌─────────────────────────┬────────┐
    │                         │  0x52  │  row 7   ← top
    │                         │  0x53  │  row 6
    │      8 x 8 clip grid    │  0x54  │  row 5
    │                         │  0x55  │  row 4
    │   note = row * 8 + col  │  0x56  │  row 3
    │   row 0 = BOTTOM        │  0x57  │  row 2
    │   col 0 = LEFT          │  0x58  │  row 1
    │                         │  0x59  │  row 0   ← bottom
    ├─────────────────────────┼────────┤
    │ 0x64 .. 0x6B  (bottom)  │  0x62  │  Shift
    └─────────────────────────┴────────┘

THE RIGHT-HAND COLUMN — the part that kept going wrong

Eight buttons, 0x52 at the TOP descending to 0x59 at the BOTTOM. Confirmed on
the appliance 2026-08-27 by direct observation of which physical button sends
0x59: the bottom one.

All EIGHT are scene launchers, one per grid row. 0x59 carries "Stop All Clips"
as its printed label, but that function is a SHIFT layer — the bench has always
required Shift+0x59 to stop everything, and Shift+0x59 held for three seconds
to clear. So 0x59 pressed alone is free, and is scene launch for row 0.

Eight buttons and eight rows: "there is the obvious exact right amount of
buttons." Any mapping that leaves a row without a launcher, or that pairs a
button with a row it does not sit beside, is wrong on its face.

The vertical flip is the whole trap. Buttons are listed top-to-bottom in
ascending note order; grid rows are numbered bottom-to-top. So the pairing is
`row = 7 - index`, and the two orderings run opposite ways. That is not a
convention anyone chose — it is Akai's note layout meeting `pad_note`'s
bottom-up rows, and it has to be written down exactly once, here.
"""

from __future__ import annotations

from control_registry import (
    required_note,
    scene_column_notes,
    track_button_notes,
)
from control_registry import GRID_COLS, GRID_NOTE_MAX, GRID_NOTE_MIN, GRID_ROWS

# --- mk1 --------------------------------------------------------------------
#: Right-hand column, TOP to BOTTOM. Index 0 is the top button.
SCENE_COLUMN_MK1: tuple[int, ...] = scene_column_notes("mk1")
#: Printed "Stop All Clips" — the bottom button. Only stops all WITH Shift.
NOTE_STOP_ALL_CLIPS_MK1 = required_note("stop_all_clips", "mk1")
NOTE_SHIFT_MK1 = required_note("shift", "mk1")
#: Bottom row of eight, left to right.
TRACK_BUTTON_NOTES_MK1: tuple[int, ...] = track_button_notes("mk1")

# --- mk2 --------------------------------------------------------------------
SCENE_COLUMN_MK2: tuple[int, ...] = scene_column_notes("mk2")
NOTE_STOP_ALL_CLIPS_MK2 = required_note("stop_all_clips", "mk2")
NOTE_SHIFT_MK2 = required_note("shift", "mk2")

assert len(SCENE_COLUMN_MK1) == GRID_ROWS, "one scene button per grid row"
assert len(SCENE_COLUMN_MK2) == GRID_ROWS, "one scene button per grid row"
assert SCENE_COLUMN_MK1[-1] == NOTE_STOP_ALL_CLIPS_MK1
assert SCENE_COLUMN_MK2[-1] == NOTE_STOP_ALL_CLIPS_MK2


def scene_column(apc_label: str) -> tuple[int, ...]:
    """The eight right-hand buttons, top to bottom."""
    return SCENE_COLUMN_MK2 if apc_label == "mk2" else SCENE_COLUMN_MK1


def row_for_scene_index(index: int) -> int:
    """Grid row beside the button at `index` counted from the TOP.

    Buttons ascend in note order downward; rows ascend upward. Hence the flip.
    """
    index = int(index)
    if not 0 <= index < GRID_ROWS:
        raise ValueError(f"scene button index out of range: {index}")
    return (GRID_ROWS - 1) - index


def scene_index_for_row(row: int) -> int:
    """Inverse of `row_for_scene_index`. Every row has a button."""
    row = int(row)
    if not 0 <= row < GRID_ROWS:
        raise ValueError(f"grid row out of range: {row}")
    return (GRID_ROWS - 1) - row


def row_for_scene_note(scene_notes: tuple[int, ...], note: int) -> int | None:
    """Grid row for a scene-column note, or None if it is not one."""
    try:
        return row_for_scene_index(scene_notes.index(int(note)))
    except ValueError:
        return None


def scene_note_for_row(scene_notes: tuple[int, ...], row: int) -> int | None:
    index = scene_index_for_row(row)
    return scene_notes[index] if index < len(scene_notes) else None


def is_stop_all(apc_label: str, note: int) -> bool:
    """Is this the button whose SHIFT layer stops everything?

    True regardless of whether Shift is held — the caller decides that. Kept
    here so nobody re-derives "the last one in the column" by hand.
    """
    stop = NOTE_STOP_ALL_CLIPS_MK2 if apc_label == "mk2" else NOTE_STOP_ALL_CLIPS_MK1
    return int(note) == stop


def scene_press_row(
    note: int,
    *,
    scene_notes: tuple[int, ...],
    apc_label: str,
    shift_held: bool,
) -> int | None:
    """Row this press should launch, or None if it is not a scene press.

    The bottom button wears two hats: scene launch for row 0, and — only while
    Shift is held — Stop All Clips. Deciding that here rather than by the order
    of `if` statements in the bench event loop is deliberate: the old code
    resolved it by never putting the button in the scene column at all, which
    silenced row 0 entirely and could not be tested without a MIDI device.

    Shift must be held FIRST. That is how the combo has always been documented
    ("Shift+StopAll"), and the alternative — retroactively withdrawing a scene
    launch when Shift arrives second — would mean launching a row and then
    un-launching it.
    """
    if shift_held and is_stop_all(apc_label, note):
        return None
    return row_for_scene_note(scene_notes, note)
