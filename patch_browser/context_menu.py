"""Context menu targets and actions for touch patch browser long-press."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from patch_browser.favorites_index import DEFAULT_FAVORITES_FOLDER
from patch_browser.patch_metadata import INSTRUMENT_VOCAB
from patch_browser.patch_scanner import favorites_folder_matches

ContextKind = Literal["library_folder", "patch", "qa_folder"]


@dataclass(frozen=True)
class ContextTarget:
    kind: ContextKind
    category: str = ""
    inner_segments: tuple[str, ...] = ()
    folder_name: str = ""
    patch: dict | None = None


def build_context_actions(
    target: ContextTarget,
    *,
    is_favorited: bool,
) -> list[tuple[str, str]]:
    """Return (action_id, label) rows for the primary context menu."""
    if target.kind == "library_folder":
        return [
            ("add_all_liked", f"Add all to {DEFAULT_FAVORITES_FOLDER}"),
            ("add_all_pick_folder", "Add all to folder…"),
        ]
    if target.kind == "patch":
        actions: list[tuple[str, str]] = []
        if is_favorited:
            actions.append(("unfavorite", "Remove from Quick Access"))
            actions.append(("move_pick_folder", "Move to folder…"))
        else:
            actions.append(("favorite_liked", f"Add to {DEFAULT_FAVORITES_FOLDER}"))
            actions.append(("add_pick_folder", "Add to folder…"))
        actions.append(("set_instrument_pick", "Set instrument…"))
        return actions
    if target.kind == "qa_folder":
        actions = [("qa_new_subfolder", "New subfolder")]
        name = target.folder_name.strip()
        if (
            name
            and name != DEFAULT_FAVORITES_FOLDER
            and len(target.inner_segments) <= 1
        ):
            actions.append(("qa_rename", "Rename folder"))
            actions.append(("qa_delete", "Delete folder"))
        return actions
    return []


def folder_picker_actions(folders: list[str]) -> list[tuple[str, str]]:
    return [(f"pick_folder:{name}", name) for name in folders]


def instrument_picker_actions() -> list[tuple[str, str]]:
    return [(f"pick_instrument:{name}", name.capitalize()) for name in INSTRUMENT_VOCAB]


def qa_folder_display_name(target: ContextTarget) -> str:
    if target.folder_name:
        return target.folder_name
    return "Quick Access"


def is_qa_browse(category: str) -> bool:
    return favorites_folder_matches(category)
