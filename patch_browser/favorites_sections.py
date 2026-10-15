"""Favorites list sections: which instrument group a favorite is shown under.

This module is the only place that decides a favorite's group. The order the
groups appear in is ``patch_metadata.INSTRUMENT_DISPLAY_ORDER``, the one
instrument order on screen. ``PatchScanner.favorite_groups`` resolves the
inputs (it owns the metadata index and the favorites index) and calls
``favorite_group``; the touch browser only lays out what comes back. A guard test refuses a second
decision site.

Where the instrument comes from. Surge writes a ``category`` attribute into each
.fxp's ``<meta>`` element, but it is not an instrument: across the 3,008
library .fxp files checked on 2026-10-13 it matched the patch's folder only 235
times, and its commonest
values are author or pack names ("Giana Brotherz", "Inigo Kennedy") and styles
("Monosynth (hard)"). The browser already has a per-patch instrument,
``instrument_primary``, set at scan time by ``PatchMetadataIndex``: the user's
own choice (long-press, Set instrument...), else the shipped baseline
(``data/patch_metadata_baseline.json``), else the folder/name heuristic. The
filter-pane chips use the same field, so the sections and the chips agree.

A favorite is a pointer to its patch (``favorites_index``), so there is one
patch and one ``instrument_primary``: the patch's own. Before pointers a favorite
was a copy under the Quick Access folder whose own tag said nothing, and the
source's was consulted first; that second opinion is gone.
"""

from __future__ import annotations

from collections.abc import Callable

from patch_browser.patch_metadata import INSTRUMENT_DISPLAY_ORDER

OTHER_GROUP = "other"

#: Sections appear in INSTRUMENT_DISPLAY_ORDER (A-Z, "Other" last), the one
#: instrument order the screen uses; the Set instrument menu and the chips use
#: it too. The jump strip lists the sections top to bottom, so it follows.
#: A section never moves when an unrelated favorite is added.

_SECTION_LABELS: dict[str, str] = {
    "fx": "FX",
}

#: Three-letter tags for the jump strip, which is too narrow for full names.
#: Initials alone collide (Piano, Pad, Pluck, Percussion).
_SECTION_TAGS: dict[str, str] = {
    "piano": "Pno",
    "keys": "Key",
    "organ": "Org",
    "bass": "Bas",
    "lead": "Lea",
    "pad": "Pad",
    "strings": "Str",
    "brass": "Brs",
    "woodwind": "Wnd",
    "pluck": "Plk",
    "synth": "Syn",
    "sequencer": "Seq",
    "percussion": "Prc",
    "fx": "FX",
    OTHER_GROUP: "Oth",
}

HEADER_KIND = "header"


def favorite_group(*, user_instrument: str | None, primary: str | None) -> str:
    """The section a favorite is listed under. The one decision site.

    ``user_instrument``: what the user set on this patch (Set instrument...), or
    None. ``primary``: the patch's scan-time ``instrument_primary``.

    The user's choice wins, even "other". Otherwise the patch's own instrument if
    it names one in the vocabulary; anything else means "other", so it can never
    make a section.
    """
    if user_instrument:
        return user_instrument if user_instrument in INSTRUMENT_DISPLAY_ORDER else OTHER_GROUP
    if primary and primary != OTHER_GROUP and primary in INSTRUMENT_DISPLAY_ORDER:
        return primary
    return OTHER_GROUP


def favorite_row_key(patch: dict) -> str:
    """What ``PatchScanner.favorite_sections`` keys a row by: its stable key."""
    return str(patch.get("stable_key") or patch.get("path") or "")


def section_label(group: str) -> str:
    return _SECTION_LABELS.get(group, group.capitalize())


def section_tag(group: str) -> str:
    return _SECTION_TAGS.get(group, group[:3].capitalize())


def sectioned_entries(
    patch_entries: list[dict],
    group_of: Callable[[dict], str],
) -> list[dict]:
    """Patch rows regrouped under header rows, in INSTRUMENT_DISPLAY_ORDER.

    ``patch_entries`` are nav entries (``{"kind": "patch", "patch": ...}``) in
    the current favorites order; that order is kept inside each section.
    ``group_of`` maps a patch dict to its group (already resolved and cached).
    Empty sections get no header.
    """
    buckets: dict[str, list[dict]] = {}
    for entry in patch_entries:
        group = group_of(entry["patch"])
        if group not in INSTRUMENT_DISPLAY_ORDER:
            group = OTHER_GROUP
        buckets.setdefault(group, []).append(entry)
    out: list[dict] = []
    for group in INSTRUMENT_DISPLAY_ORDER:
        rows = buckets.get(group)
        if not rows:
            continue
        out.append(
            {
                "kind": HEADER_KIND,
                "group": group,
                "label": section_label(group),
                "count": len(rows),
            }
        )
        out.extend(rows)
    return out


def section_starts(entries: list[dict]) -> list[tuple[str, int]]:
    """(group, row index of its header) for each header row, top to bottom."""
    return [
        (entry["group"], index)
        for index, entry in enumerate(entries)
        if entry.get("kind") == HEADER_KIND
    ]
