"""Context menu targets and actions for touch patch browser long-press."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from patch_browser.favorites_index import is_protected_qa_folder
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
    qa_patch_count: int = 0,
) -> list[tuple[str, str]]:
    """Return (action_id, label) rows for the primary context menu."""
    if target.kind == "library_folder":
        return [
            ("add_all_qa", "Add folder to Quick Select"),
        ]
    if target.kind == "patch":
        actions: list[tuple[str, str]] = []
        if is_favorited:
            actions.append(("unfavorite", "Remove from Quick Select"))
            actions.append(("move_pick_folder", "Move to folder…"))
        else:
            actions.append(("favorite_qa", "Add to Quick Select"))
            actions.append(("add_pick_folder", "Add to folder…"))
        actions.append(("set_instrument_pick", "Set instrument…"))
        return actions
    if target.kind == "qa_folder":
        actions = [("qa_new_subfolder", "New subfolder")]
        if qa_patch_count > 0:
            actions.append(("qa_remove_all", "Remove all patches"))
        name = target.folder_name.strip()
        if name and len(target.inner_segments) <= 1 and not is_protected_qa_folder(name):
            actions.append(("qa_rename", "Rename folder"))
            actions.append(("qa_delete", "Delete folder"))
        return actions
    return []


def folder_picker_actions(folders: list[str]) -> list[tuple[str, str]]:
    """User-created Quick Select subfolders (excludes root)."""
    user = [name for name in folders if name.strip()]
    return [(f"pick_folder:{name}", name) for name in user]


def instrument_picker_actions() -> list[tuple[str, str]]:
    return [(f"pick_instrument:{name}", name.capitalize()) for name in INSTRUMENT_VOCAB]


def qa_folder_display_name(target: ContextTarget) -> str:
    if target.folder_name:
        return target.folder_name
    return "Quick Select"


def is_qa_browse(category: str) -> bool:
    return favorites_folder_matches(category)
