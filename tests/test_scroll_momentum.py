"""Shared scroll momentum — nav uses same release physics as settings modals."""

import unittest

from patch_browser.geometry import Rect
from patch_browser.scroll_widgets import ContentScrollArea, ScrollList, compute_release_velocity


class ComputeReleaseVelocityTests(unittest.TestCase):
    def test_window_average_over_recent_samples(self) -> None:
        samples = [(0.0, 0.0), (0.1, 200.0)]
        self.assertAlmostEqual(compute_release_velocity(samples), 2000.0)

    def test_too_short_window_returns_zero(self) -> None:
        samples = [(0.0, 0.0), (0.002, 40.0)]
        self.assertEqual(compute_release_velocity(samples), 0.0)

    def test_clamps_to_cap(self) -> None:
        samples = [(0.0, 0.0), (0.05, 500.0)]
        self.assertEqual(compute_release_velocity(samples, cap=800.0), 800.0)


class ScrollListMomentumTests(unittest.TestCase):
    def test_flick_coasts_with_sample_window_velocity(self) -> None:
        lst = ScrollList(Rect(0, 0, 200, 200), row_height=40)
        lst.set_items([f"row-{i}" for i in range(30)], preserve_scroll=False)
        lst.pointer_down((100, 50))
        lst.pointer_move((100, 30))
        lst.pointer_move((100, 10))
        lst.pointer_up((100, 10))
        self.assertTrue(lst._momentum_active)
        self.assertGreater(abs(lst._velocity), 0.0)

    def test_tap_does_not_coast(self) -> None:
        lst = ScrollList(Rect(0, 0, 200, 200), row_height=40)
        lst.set_items(["a", "b", "c"], preserve_scroll=False)
        lst.pointer_down((100, 50))
        lst.pointer_up((100, 50))
        self.assertFalse(lst._momentum_active)


class ContentScrollAreaMomentumTests(unittest.TestCase):
    def test_matches_shared_release_helper(self) -> None:
        area = ContentScrollArea(Rect(0, 0, 100, 80))
        area.content_height = 400
        area.pointer_down((50, 40))
        area.pointer_move((50, 10))
        area.pointer_up((50, 10))
        self.assertTrue(area._momentum_active)


if __name__ == "__main__":
    unittest.main()
