"""Instrument chip filter helpers for the touch patch browser."""

from __future__ import annotations

from typing import TYPE_CHECKING

from patch_browser.patch_metadata import INSTRUMENT_VOCAB

if TYPE_CHECKING:
    from patch_browser.patch_scanner import PatchScanner


def primary_instrument(patch: dict) -> str:
    return str(patch.get("instrument_primary") or "other")


def filter_patches_by_instrument(
    patches: list[dict],
    instrument: str | None,
) -> list[dict]:
    if not instrument:
        return list(patches)
    return [patch for patch in patches if primary_instrument(patch) == instrument]


def instrument_counts(patches: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for patch in patches:
        inst = primary_instrument(patch)
        counts[inst] = counts.get(inst, 0) + 1
    return counts


def instruments_with_patches(patches: list[dict]) -> list[str]:
    """Instruments that have at least one patch, ordered A-Z by chip label.

    Sorted here rather than by reordering INSTRUMENT_VOCAB, because that tuple
    is also the key order for classification scoring
    (`patch_metadata.classify_instrument`) — reordering it would change
    tie-breaks between instruments that score equally, which is a silent
    change to how patches are labelled, not a display change.

    Membership still comes from the vocab, so an unknown instrument_primary
    cannot introduce a chip.
    """
    counts = instrument_counts(patches)
    present = [name for name in INSTRUMENT_VOCAB if counts.get(name, 0) > 0]
    return sorted(present, key=instrument_chip_label)


def instrument_chip_label(instrument: str) -> str:
    return instrument.capitalize()


def patches_in_browse_subtree(
    scanner: PatchScanner,
    category: str,
    inner_segments: tuple[str, ...],
) -> list[dict]:
    """All patches under the current browse folder (recursive)."""
    collected: list[dict] = []
    stack: list[tuple[str, ...]] = [inner_segments]
    while stack:
        inner = stack.pop()
        collected.extend(scanner.get_patches_in_folder(category, inner))
        for name in scanner.get_subfolders(category, inner):
            stack.append(inner + (name,))
    return collected


def patches_in_folder_only(
    scanner: PatchScanner,
    category: str,
    inner_segments: tuple[str, ...],
) -> list[dict]:
    """Patches directly in one folder — not parent siblings, not recursive into children."""
    return list(scanner.get_patches_in_folder(category, inner_segments))
