"""Scene Launch button index ↔ grid row (physical alignment)."""

from __future__ import annotations

from tests import conftest  # noqa: F401

import unittest

from scripts.sooperlooper.apc_transport import (
    scene_launch_index_to_row,
    scene_row_for_note,
    scene_row_to_launch_index,
    SCENE_LAUNCH_NOTES_MK1,
)


class SceneRowMappingTests(unittest.TestCase):
    def test_top_scene_button_maps_to_upper_grid_row(self) -> None:
        self.assertEqual(scene_launch_index_to_row(0), 6)

    def test_bottom_scene_button_maps_to_bottom_grid_row(self) -> None:
        self.assertEqual(scene_launch_index_to_row(6), 0)

    def test_round_trip(self) -> None:
        for row in range(7):
            self.assertEqual(scene_launch_index_to_row(scene_row_to_launch_index(row)), row)

    def test_note_lookup_uses_physical_row(self) -> None:
        notes = SCENE_LAUNCH_NOTES_MK1
        self.assertEqual(scene_row_for_note(notes, notes[6]), 0)
        self.assertEqual(scene_row_for_note(notes, notes[0]), 6)
