"""Fling distance and the runaway guard (retuned 2026-10-13).

History: on 2026-08-14 the patch list "took off" after small scrolls; 2cb6f40
damped it, two retunes followed, and bd0e78f put every widget back on one shared
rule. On 2026-10-13 Mitch found it sluggish top to bottom. The retune adds gain
for firm flicks only; these tests pin both halves.
"""

from __future__ import annotations

import math
import unittest
from unittest import mock

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.geometry import Rect  # noqa: E402
from patch_browser.scroll_widgets import (  # noqa: E402
    ContentScrollArea,
    ScrollList,
    fling_gain,
    fling_launch_velocity,
)
from patch_browser.touch_ui_constants import (  # noqa: E402
    PATCHES_ROW_HEIGHT,
    SCROLL_FLING_BOOST_MAX,
    SCROLL_FLING_FIRM_PX_S,
    SCROLL_FLING_GENTLE_PX_S,
    SCROLL_FRICTION,
    SCROLL_VELOCITY_CAP,
)

FRAME = 1 / 60


def coast_px(launch: float) -> float:
    """Distance the nav list travels after launching at ``launch`` px/s, at 60 fps."""
    lst = ScrollList(Rect(0, 0, 250, 330), row_height=PATCHES_ROW_HEIGHT)
    lst.set_items(["x"] * 5000, preserve_scroll=False)
    start = 100_000.0
    lst._scroll_pixels = start
    lst._velocity = launch
    lst._momentum_active = True
    for _ in range(100_000):
        if not lst._momentum_active:
            break
        lst.tick(FRAME)
    return lst._scroll_pixels - start


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def drag(lst, points: list[tuple[float, int]], release_at: float, clock: Clock) -> None:
    """Finger down at the first point, motion through the rest, lift at release_at."""
    t0, y0 = points[0]
    clock.t = t0
    lst.pointer_down((100, y0))
    for t, y in points[1:]:
        clock.t = t
        lst.pointer_move((100, y))
    clock.t = release_at
    lst.pointer_up((100, points[-1][1]))


class FlingGainCurve(unittest.TestCase):
    def test_gentle_releases_are_untouched(self) -> None:
        for v in (0.0, 50.0, 300.0, SCROLL_FLING_GENTLE_PX_S):
            self.assertEqual(fling_gain(v), 1.0)
            self.assertEqual(fling_gain(-v), 1.0)

    def test_firm_flicks_get_the_full_boost(self) -> None:
        for v in (SCROLL_FLING_FIRM_PX_S, SCROLL_VELOCITY_CAP):
            self.assertEqual(fling_gain(v), SCROLL_FLING_BOOST_MAX)

    def test_gain_ramps_linearly_between_gentle_and_firm(self) -> None:
        mid = (SCROLL_FLING_GENTLE_PX_S + SCROLL_FLING_FIRM_PX_S) / 2
        self.assertAlmostEqual(fling_gain(mid), (1.0 + SCROLL_FLING_BOOST_MAX) / 2)

    def test_launch_speed_rises_with_finger_speed(self) -> None:
        previous = 0.0
        for v in range(0, int(SCROLL_VELOCITY_CAP) + 1, 25):
            launch = v * fling_gain(v)
            self.assertGreaterEqual(launch, previous)
            previous = launch


class FlingDistance(unittest.TestCase):
    """The before/after table in the PR comes from coast_px."""

    def test_gentle_drag_coasts_as_before(self) -> None:
        # 300 px/s released: ~105 px, under two rows, the same as before the retune.
        self.assertLess(coast_px(300 * fling_gain(300)), 2 * PATCHES_ROW_HEIGHT)
        self.assertEqual(coast_px(300 * fling_gain(300)), coast_px(300))

    def test_firm_flick_covers_much_more_of_the_list(self) -> None:
        before = coast_px(SCROLL_FLING_FIRM_PX_S)
        after = coast_px(SCROLL_FLING_FIRM_PX_S * fling_gain(SCROLL_FLING_FIRM_PX_S))
        self.assertLess(before, 16 * PATCHES_ROW_HEIGHT)  # ~873 px: the sluggish case
        self.assertGreater(after, 35 * PATCHES_ROW_HEIGHT)  # ~2189 px
        self.assertAlmostEqual(after / before, SCROLL_FLING_BOOST_MAX, delta=0.05)

    def test_no_flick_can_exceed_the_bound(self) -> None:
        bound = SCROLL_VELOCITY_CAP * SCROLL_FLING_BOOST_MAX / SCROLL_FRICTION
        worst = coast_px(fling_launch_velocity([(0.0, 0.0)], (0.009, 1e6)))
        self.assertLessEqual(worst, bound * 1.03)  # discrete 60 fps steps overshoot ~1.4%
        self.assertGreater(worst, bound * 0.9)
        # In absolute terms: no single flick goes further than ~52 rows. Raising
        # the cap, the boost or lowering friction past this needs a decision.
        self.assertLess(worst, 3000.0)


class RunawayGuard(unittest.TestCase):
    """The case that "took off": a stale velocity launching the list on release."""

    def setUp(self) -> None:
        self.clock = Clock()
        patcher = mock.patch("patch_browser.scroll_widgets.time.time", side_effect=self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _list(self) -> ScrollList:
        lst = ScrollList(Rect(0, 0, 250, 330), row_height=PATCHES_ROW_HEIGHT)
        lst.set_items(["x"] * 200, preserve_scroll=False)
        return lst

    def test_fast_drag_then_hold_then_lift_does_not_coast(self) -> None:
        """A still finger sends no motion events, so the last samples are the fast
        ones. Lifting 200 ms later must not launch the list from them."""
        lst = self._list()
        fast = [(1000.0 + i * 0.01, 300 - i * 30) for i in range(8)]  # 3000 px/s
        drag(lst, fast, release_at=fast[-1][0] + 0.2, clock=self.clock)
        self.assertFalse(lst._momentum_active)
        self.assertEqual(lst._velocity, 0.0)

    def test_the_same_drag_lifted_at_once_does_fling(self) -> None:
        """The positive control for the test above: same samples, no hold."""
        lst = self._list()
        fast = [(1000.0 + i * 0.01, 300 - i * 30) for i in range(8)]
        drag(lst, fast, release_at=fast[-1][0] + 0.008, clock=self.clock)
        self.assertTrue(lst._momentum_active)
        self.assertGreater(lst._velocity, SCROLL_FLING_FIRM_PX_S)

    def test_slow_drag_lifted_at_once_stays_gentle(self) -> None:
        lst = self._list()
        slow = [(1000.0 + i * 0.02, 300 - i * 6) for i in range(10)]  # 300 px/s
        drag(lst, slow, release_at=slow[-1][0] + 0.008, clock=self.clock)
        self.assertLessEqual(abs(lst._velocity), SCROLL_FLING_GENTLE_PX_S)

    def test_a_glitched_sample_is_capped(self) -> None:
        launch = fling_launch_velocity([(1000.0, 0.0)], (1000.009, 50_000.0))
        self.assertEqual(launch, SCROLL_VELOCITY_CAP * SCROLL_FLING_BOOST_MAX)

    def test_content_scroll_area_uses_the_same_rule(self) -> None:
        area = ContentScrollArea(Rect(0, 0, 300, 300))
        area.content_height = 6000
        self.clock.t = 1000.0
        area.pointer_down((100, 290))
        for i in range(1, 8):
            self.clock.t = 1000.0 + i * 0.01
            area.pointer_move((100, 290 - i * 30))
        self.clock.t += 0.2
        area.pointer_up((100, 290 - 7 * 30))
        self.assertFalse(area._momentum_active)

    def test_window_math(self) -> None:
        samples = [(0.0, 0.0), (0.05, 100.0), (0.10, 200.0)]
        v = fling_launch_velocity(samples, (0.11, 200.0))
        measured = 200.0 / 0.11
        self.assertTrue(math.isclose(v, measured * fling_gain(measured)))


if __name__ == "__main__":
    unittest.main()
