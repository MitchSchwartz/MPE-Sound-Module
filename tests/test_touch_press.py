"""Tests for TouchPressState — shared pressed feedback tracking."""

import unittest

from patch_browser.touch_press import TouchPressState


class TouchPressStateTests(unittest.TestCase):
    def test_default_clear(self) -> None:
        state = TouchPressState()
        self.assertIsNone(state.active_id)
        self.assertFalse(state.is_pressed("any"))

    def test_set_and_is_pressed(self) -> None:
        state = TouchPressState()
        state.set("btn:done")
        self.assertTrue(state.is_pressed("btn:done"))
        self.assertFalse(state.is_pressed("btn:cancel"))

    def test_clear(self) -> None:
        state = TouchPressState()
        state.set("row:1")
        state.clear()
        self.assertIsNone(state.active_id)
        self.assertFalse(state.is_pressed("row:1"))

    def test_set_none(self) -> None:
        state = TouchPressState()
        state.set("a")
        state.set(None)
        self.assertIsNone(state.active_id)


if __name__ == "__main__":
    unittest.main()
