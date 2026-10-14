"""One instrument order on screen (2026-10-13): A-Z, "Other" last, for the
favorites sections and their jump strip, the Set instrument menu, and the
filter chips. Defined once, in patch_metadata."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.context_menu import instrument_picker_actions  # noqa: E402
from patch_browser.favorites_sections import HEADER_KIND, sectioned_entries  # noqa: E402
from patch_browser.instrument_filter import instruments_with_patches  # noqa: E402
from patch_browser.patch_metadata import INSTRUMENT_DISPLAY_ORDER, INSTRUMENT_VOCAB  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

#: Pinned in full: a rule like "sorted" would agree with a mutant that sorts
#: "other" in with the rest.
EXPECTED = (
    "bass",
    "brass",
    "fx",
    "keys",
    "lead",
    "organ",
    "pad",
    "percussion",
    "piano",
    "pluck",
    "sequencer",
    "strings",
    "synth",
    "woodwind",
    "other",
)


class TheOrder(unittest.TestCase):
    def test_a_to_z_with_other_last(self) -> None:
        self.assertEqual(INSTRUMENT_DISPLAY_ORDER, EXPECTED)

    def test_every_instrument_once(self) -> None:
        self.assertEqual(sorted(INSTRUMENT_DISPLAY_ORDER), sorted(INSTRUMENT_VOCAB))

    def test_the_classifier_order_is_untouched(self) -> None:
        """INSTRUMENT_VOCAB is the scoring tie-break order; display must not
        reorder it (a silent relabelling of patches)."""
        self.assertEqual(INSTRUMENT_VOCAB[:4], ("piano", "keys", "organ", "bass"))
        self.assertNotEqual(INSTRUMENT_VOCAB, INSTRUMENT_DISPLAY_ORDER)


class EveryListUsesIt(unittest.TestCase):
    def test_set_instrument_menu(self) -> None:
        rows = instrument_picker_actions()
        self.assertEqual([a.split(":", 1)[1] for a, _label in rows], list(EXPECTED))
        self.assertEqual(rows[0], ("pick_instrument:bass", "Bass"))
        self.assertEqual(rows[-1], ("pick_instrument:other", "Other"))

    def test_favorites_sections(self) -> None:
        # One favorite per instrument, fed in classifier order.
        entries = [
            {"kind": "patch", "patch": {"name": name, "path": f"/qa/{name}.fxp"}, "label": name}
            for name in INSTRUMENT_VOCAB
        ]
        out = sectioned_entries(entries, lambda patch: patch["name"])
        self.assertEqual([e["group"] for e in out if e["kind"] == HEADER_KIND], list(EXPECTED))

    def test_filter_chips(self) -> None:
        patches = [{"instrument_primary": name} for name in INSTRUMENT_VOCAB]
        self.assertEqual(instruments_with_patches(patches), list(EXPECTED))
        # A subset keeps the order, "other" still last.
        subset = [{"instrument_primary": n} for n in ("other", "piano", "bass", "fx")]
        self.assertEqual(instruments_with_patches(subset), ["bass", "fx", "piano", "other"])


class OneOrdering(unittest.TestCase):
    """Guard: a second instrument ordering in patch_browser fails here."""

    def _sources(self):
        for path in sorted((REPO / "patch_browser").rglob("*.py")):
            yield path.relative_to(REPO).as_posix(), path.read_text(errors="replace")

    def test_defined_in_one_place(self) -> None:
        definers = [
            rel for rel, text in self._sources()
            if re.search(r"^\s*INSTRUMENT_DISPLAY_ORDER\b[^\n=]*=", text, re.M)
        ]
        self.assertEqual(definers, ["patch_browser/patch_metadata.py"])

    def test_no_list_is_built_from_the_classifier_order(self) -> None:
        """INSTRUMENT_VOCAB is read only where it is defined (the classifier and
        the order itself). Any other module listing instruments from it is
        a second ordering."""
        users = [rel for rel, text in self._sources() if "INSTRUMENT_VOCAB" in text]
        self.assertEqual(users, ["patch_browser/patch_metadata.py"])

    def test_the_instrument_lists_do_not_sort(self) -> None:
        """The modules that build an instrument list take the order as it is;
        a sort there is a second opinion (the chips used to sort A-Z with
        "Other" in the middle)."""
        for rel in (
            "patch_browser/context_menu.py",
            "patch_browser/favorites_sections.py",
            "patch_browser/instrument_filter.py",
            "patch_browser/touch_browser_instruments.py",
            "patch_browser/jump_strip.py",
        ):
            text = (REPO / rel).read_text()
            with self.subTest(rel=rel):
                self.assertNotRegex(text, r"\bsorted\(|\.sort\(")


if __name__ == "__main__":
    unittest.main()
