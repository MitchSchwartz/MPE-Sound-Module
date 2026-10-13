"""Favorites list sections: which instrument group a favorite is shown under.

This module is the only place that decides a favorite's group and the order the
groups appear in. ``PatchScanner.favorite_groups`` resolves the inputs (it owns
the metadata index and the favorites index) and calls ``favorite_group``; the
touch browser only lays out what comes back. A guard test refuses a second
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

A favorite is a *copy* under the Quick Access folder, so its own
``instrument_primary`` was classified from the copy's path, whose folder says
nothing about the instrument. The source patch's instrument, classified from
its real library folder, is the better signal, and is used unless the user set
an instrument on the copy itself.
"""

from __future__ import annotations

from collections.abc import Callable

from patch_browser.patch_metadata import INSTRUMENT_VOCAB

OTHER_GROUP = "other"

#: Fixed section order: the keyboard family first, then bass, lead and pad, the
#: acoustic families, then synth-type groups, and "Other" always last. A fixed
#: order (not A-Z) keeps related instruments next to each other, and a section
#: never moves when an unrelated favorite is added. A guard test keeps this a
#: permutation of INSTRUMENT_VOCAB, so a new instrument cannot be left out.
FAVORITES_SECTION_ORDER: tuple[str, ...] = (
    "piano",
    "keys",
    "organ",
    "bass",
    "lead",
    "pad",
    "strings",
    "brass",
    "woodwind",
    "pluck",
    "synth",
    "sequencer",
    "percussion",
    "fx",
    OTHER_GROUP,
)

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


def favorite_group(
    *,
    user_instrument: str | None,
    source_primary: str | None,
    own_primary: str | None,
) -> str:
    """The section a favorite is listed under. The one decision site.

    ``user_instrument``: what the user set on this favorite (Set instrument...),
    or None. ``source_primary``: the library patch it was copied from, or None
    when the copy cannot be traced. ``own_primary``: the copy's own scan-time
    instrument.

    The user's choice wins, even "other". Otherwise the first of source, then
    copy, that names a real instrument; neither does means "other". Anything
    outside the vocabulary counts as no answer, so it can never make a section.
    """
    if user_instrument:
        return user_instrument if user_instrument in FAVORITES_SECTION_ORDER else OTHER_GROUP
    for candidate in (source_primary, own_primary):
        if candidate and candidate != OTHER_GROUP and candidate in FAVORITES_SECTION_ORDER:
            return candidate
    return OTHER_GROUP


def section_label(group: str) -> str:
    return _SECTION_LABELS.get(group, group.capitalize())


def section_tag(group: str) -> str:
    return _SECTION_TAGS.get(group, group[:3].capitalize())


def sectioned_entries(
    patch_entries: list[dict],
    group_of: Callable[[dict], str],
) -> list[dict]:
    """Patch rows regrouped under header rows, in FAVORITES_SECTION_ORDER.

    ``patch_entries`` are nav entries (``{"kind": "patch", "patch": ...}``) in
    the current favorites order; that order is kept inside each section.
    ``group_of`` maps a patch dict to its group (already resolved and cached).
    Empty sections get no header.
    """
    buckets: dict[str, list[dict]] = {}
    for entry in patch_entries:
        group = group_of(entry["patch"])
        if group not in FAVORITES_SECTION_ORDER:
            group = OTHER_GROUP
        buckets.setdefault(group, []).append(entry)
    out: list[dict] = []
    for group in FAVORITES_SECTION_ORDER:
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
