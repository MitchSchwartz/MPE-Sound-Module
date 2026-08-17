"""Tests for the memoising font wrapper that keeps the draw loop off a full core."""

from __future__ import annotations

import unittest

from patch_browser.font_cache import CachedFont, _colour_key


class FakeSurface:
    def __init__(self, tag: str) -> None:
        self.tag = tag


class FakeFont:
    """Counts rasterisations so the tests can assert work avoided, not just results."""

    def __init__(self) -> None:
        self.render_calls = 0
        self.size_calls = 0
        self.height = 22

    def render(self, text, antialias, color, background=None):
        self.render_calls += 1
        return FakeSurface(f"{text}|{antialias}|{color}|{background}")

    def size(self, text):
        self.size_calls += 1
        return (len(text) * 7, self.height)

    def get_height(self):
        return self.height


class RenderCacheTests(unittest.TestCase):
    def test_identical_render_rasterises_once(self) -> None:
        font = FakeFont()
        cached = CachedFont(font)
        first = cached.render("Quick Select", True, (255, 255, 255))
        for _ in range(60):  # one second of frames
            cached.render("Quick Select", True, (255, 255, 255))
        self.assertEqual(font.render_calls, 1)
        self.assertIs(cached.render("Quick Select", True, (255, 255, 255)), first)
        self.assertEqual(cached.render_misses, 1)
        self.assertEqual(cached.render_hits, 60 + 1)

    def test_colour_is_part_of_the_key(self) -> None:
        """A theme change must not keep serving the old colour."""
        font = FakeFont()
        cached = CachedFont(font)
        cached.render("Vol", True, (255, 255, 255))
        cached.render("Vol", True, (255, 0, 0))
        self.assertEqual(font.render_calls, 2)

    def test_text_and_antialias_are_part_of_the_key(self) -> None:
        font = FakeFont()
        cached = CachedFont(font)
        cached.render("A", True, (1, 2, 3))
        cached.render("B", True, (1, 2, 3))
        cached.render("A", False, (1, 2, 3))
        self.assertEqual(font.render_calls, 3)

    def test_background_variant_is_distinct(self) -> None:
        font = FakeFont()
        cached = CachedFont(font)
        cached.render("x", True, (1, 2, 3))
        cached.render("x", True, (1, 2, 3), (9, 9, 9))
        self.assertEqual(font.render_calls, 2)

    def test_size_is_cached(self) -> None:
        """wrap_text_lines calls size() per word, per frame."""
        font = FakeFont()
        cached = CachedFont(font)
        for _ in range(30):
            cached.size("Bowed String")
        self.assertEqual(font.size_calls, 1)

    def test_lru_evicts_oldest_and_stays_bounded(self) -> None:
        font = FakeFont()
        cached = CachedFont(font, max_entries=3)
        for i in range(5):
            cached.render(f"t{i}", True, (0, 0, 0))
        self.assertEqual(len(cached._render_cache), 3)
        # t0/t1 evicted; t4 still resident.
        before = font.render_calls
        cached.render("t4", True, (0, 0, 0))
        self.assertEqual(font.render_calls, before)
        cached.render("t0", True, (0, 0, 0))
        self.assertEqual(font.render_calls, before + 1)

    def test_clear_drops_entries(self) -> None:
        font = FakeFont()
        cached = CachedFont(font)
        cached.render("x", True, (0, 0, 0))
        cached.clear()
        cached.render("x", True, (0, 0, 0))
        self.assertEqual(font.render_calls, 2)

    def test_unknown_attributes_pass_through(self) -> None:
        """Call sites use get_height/get_linesize on these fonts."""
        cached = CachedFont(FakeFont())
        self.assertEqual(cached.get_height(), 22)

    def test_surfaces_are_shared_not_copied(self) -> None:
        """Documented hazard: callers must treat the surface as read-only."""
        cached = CachedFont(FakeFont())
        a = cached.render("shared", True, (1, 1, 1))
        b = cached.render("shared", True, (1, 1, 1))
        self.assertIs(a, b)


class ColourKeyTests(unittest.TestCase):
    def test_handles_tuples_ints_strings_and_none(self) -> None:
        self.assertEqual(_colour_key((1, 2, 3)), (1, 2, 3))
        self.assertEqual(_colour_key(0xFF00FF), 0xFF00FF)
        self.assertEqual(_colour_key("red"), "red")
        self.assertIsNone(_colour_key(None))

    def test_rgba_sequence_is_distinguished_from_rgb(self) -> None:
        self.assertNotEqual(_colour_key((1, 2, 3)), _colour_key((1, 2, 3, 128)))

    def test_unhashable_colour_does_not_explode(self) -> None:
        class Weird:
            def __repr__(self) -> str:
                return "weird-colour"

        self.assertEqual(_colour_key(Weird()), "weird-colour")


if __name__ == "__main__":
    unittest.main()
