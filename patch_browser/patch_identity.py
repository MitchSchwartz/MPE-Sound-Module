"""Stable patch identity and folder-tree helpers for PatchScanner."""

from __future__ import annotations

from pathlib import Path

PATCH_ROOT_LABELS = {
    "factory": "patches_factory",
    "thirdparty": "patches_3rdparty",
    "user": None,
}


def patch_root_label(patch_dir: Path) -> str:
    """Short label for a Surge patch root directory."""
    name = patch_dir.name.lower()
    if name == "patches_factory":
        return "factory"
    if name == "patches_3rdparty":
        return "thirdparty"
    return "user"


def stable_key_for_relative_path(root_label: str, rel_path: Path) -> str:
    """Canonical patch key: ``factory:Bass/Sub/Lead 1``."""
    rel_stem = rel_path.with_suffix("").as_posix()
    return f"{root_label}:{rel_stem}"


def category_and_inner_segments(folder_rel: Path) -> tuple[str, tuple[str, ...]]:
    """
    Top-level browse category and nested folder segments under it.

    ``Bass/Sub`` → (``Bass``, (``Sub``,)); root-level file → (``Uncategorized``, ()).
    """
    parts = folder_rel.parts
    if not parts:
        return "Uncategorized", ()
    return parts[0], parts[1:]


def patch_browse_subtitle(patch: dict) -> str:
    """Human folder path for list subtitles (category + nested segments)."""
    category = patch.get("category", "")
    inner = patch.get("inner_segments") or ()
    if inner:
        return "/".join((category,) + tuple(inner))
    return category


def patch_list_subtitle(patch: dict) -> str:
    """List row subtitle: primary instrument + folder path."""
    primary = patch.get("instrument_primary") or "other"
    folder = patch_browse_subtitle(patch)
    return f"{primary} · {folder}"


def build_folder_tree(
    patches_by_category: dict[str, list[dict]],
) -> dict[str, dict]:
    """
    Nested folder tree per top-level category.

    Each node: ``{"patches": [...], "children": {name: node}}``.
    Patches are stored at the node matching their ``inner_segments``.
    """
    tree: dict[str, dict] = {}

    def _ensure_node(category: str, inner: tuple[str, ...]) -> dict:
        if category not in tree:
            tree[category] = {"patches": [], "children": {}}
        node = tree[category]
        for segment in inner:
            node = node["children"].setdefault(
                segment, {"patches": [], "children": {}}
            )
        return node

    for category, patch_list in patches_by_category.items():
        for patch in patch_list:
            inner = tuple(patch.get("inner_segments") or ())
            node = _ensure_node(category, inner)
            node["patches"].append(patch)

    return tree


def make_patch_entry(
    *,
    name: str,
    path: Path,
    patch_dir: Path,
    root_label: str,
    category_override: str | None = None,
) -> dict:
    """Build a patch dict with path-based identity fields."""
    rel_path = path.relative_to(patch_dir)
    folder_rel = rel_path.parent
    category, inner_segments = category_and_inner_segments(folder_rel)
    if category_override is not None:
        category = category_override

    folder_segments = folder_rel.parts
    return {
        "name": name,
        "path": str(path),
        "category": category,
        "folder_segments": folder_segments,
        "inner_segments": inner_segments,
        "relative_path": rel_path.as_posix(),
        "patch_root": root_label,
        "stable_key": stable_key_for_relative_path(root_label, rel_path),
    }
