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


def instruments_with_patches(patches: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for patch in patches:
        inst = primary_instrument(patch)
        counts[inst] = counts.get(inst, 0) + 1
    return [name for name in INSTRUMENT_VOCAB if counts.get(name, 0) > 0]


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
