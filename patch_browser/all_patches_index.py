"""Flat sorted patch list and A–Z jump index for the All patches view."""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_browser.patch_scanner import PatchScanner

AZ_RAIL_LETTERS = ("#",) + tuple(string.ascii_uppercase)


def first_sort_letter(name: str) -> str:
    """Bucket key for A–Z rail (non-alpha → '#')."""
    stripped = (name or "").strip()
    if not stripped:
        return "#"
    ch = stripped[0].upper()
    return ch if ch.isalpha() else "#"


def build_flat_patch_list(scanner: PatchScanner) -> tuple[list[dict], dict[str, int]]:
    """
    Return all patches sorted by name and letter → first row index for scroll jump.

    Each patch dict is the scanner entry: name, path, category.
    """
    patches: list[dict] = []
    with scanner.scan_lock:
        for category in scanner.patches.values():
            patches.extend(category)

    patches.sort(key=lambda p: (p["name"].casefold(), p.get("path", "")))

    letter_index: dict[str, int] = {}
    for index, patch in enumerate(patches):
        letter = first_sort_letter(patch["name"])
        if letter not in letter_index:
            letter_index[letter] = index

    return patches, letter_index
