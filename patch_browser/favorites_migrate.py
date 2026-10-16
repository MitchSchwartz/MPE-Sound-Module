"""Migrate favorites from copies to pointers (#181).

Before: every favorite is a copy of a patch under the favorites folder of the user
patch root, and the settings made while playing it live under the *copy's* stable key.
After: the favorites index holds stable keys, and the settings live under the key of
the patch the favorite points at.

What it does, in this order (``plan`` computes all of it; nothing is written by a
dry run):

1. Classify every ``.fxp`` in the favorites tree by its bytes, never its name:

   - identical to a library file -> a **pointer** to that file's key; the copy goes;
   - not identical, and named in ``resave_names`` -> a **pointer** to the stock patch
     of the same name (a plain re-save: Surge rewrote the header and nothing else);
   - not identical, and named in ``tuned_names`` -> a **tuned** patch: the file moves
     to ``<user root>/<tuned folder>/`` as a real file and the favorite points at it;
   - not identical otherwise -> a **user patch**: it stays where it is and is
     favorited under its own key (nothing to re-key).

   Files named in ``tuned_names`` that sit elsewhere under the user root (a patch the
   index never held) are picked up the same way and become favorites.
2. Re-key every per-patch store from the copy's key to the target key. Where both
   hold a value, the favorite's (the copy's) wins, field by field; fields only the
   target has are kept.
3. Rewrite the index as version 2 (keys, folders, order) and remember the user patches
   in ``imported`` so the scan does not treat them as new.

``apply`` backs up the index and every store into a timestamped run directory first,
moves files with git inside the personal library checkout and commits only those
moves, then writes the stores and the index. It writes a manifest and a before
snapshot there; ``verify`` and ``undo`` read them. A second ``apply`` finds nothing to
do. Run with the patch browser stopped: it holds its stores in memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from patch_browser.favorites_backup import BACKUP_DIR_NAME
from patch_browser.favorites_index import FavoritesIndex, normalize_key
from patch_browser.json_store import atomic_write_json
from patch_browser.library_ops import (
    STORE_FILES,
    LibraryError,
    _git,
    _key_container,
    default_scan_roots,
    default_store_paths,
)
from patch_browser.patch_identity import patch_root_label, stable_key_for_relative_path

TUNED_FOLDER = "Tuned Patches"
#: Patches the owner tuned in Surge on a computer (real MPE changes): they move to
#: ``TUNED_FOLDER`` and the favorites point at them.
TUNED_NAMES = (
    "Bass Ocarina",
    "Bell Piano",
    "Cyber Pad",
    "Duduk",
    "Koala 1",
    "Legato Bass",
    "Res Wave Shift",
    "Reso Brassy",
    "Glassy Wind",
)
#: Re-saves with no change in sound: they become pointers to the stock patch.
RESAVE_NAMES = ("Bowed String", "Dark Cello", "Overdriven Guitar + Wah")

MANIFEST = "manifest.json"
BEFORE = "before.json"
RUN_PREFIX = "pointers-"
#: Per-patch stores re-keyed here. The favorites store is the index and is rewritten.
REKEY_STORES = tuple(STORE_FILES)

#: The touch browser holds its stores in memory and writes them back on exit; a
#: migration under it is undone by its next save. Both are overridable from the
#: environment for a differently named deployment.
TOUCH_UNIT_DEFAULT = "touch-patch-browser.service"
TOUCH_SCRIPT_DEFAULT = "touch_patch_browser.py"

#: Fields of a settings entry that describe the *copy* or were captured from it, not
#: choices the owner made. The favorite's value does not override the target's for
#: these (``baseline`` only where the copy is a different sound than the target).
DESCRIPTIVE_FIELDS = {"metadata": ("path", "path_segments")}
CAPTURED_FIELDS = {"hold": ("baseline",)}

POINTER = "pointer"
RESAVE = "resave-pointer"
TUNED = "tuned"
USER = "user-patch"
INDEX_ONLY = "index-only"


@dataclass
class Row:
    """One favorite, before and after."""

    klass: str
    file_rel: str | None  # posix path inside the favorites folder (None: index-only)
    copy_key: str | None
    old_key: str | None  # key the old index filed it under
    after_key: str
    folder: str
    added_at: str | None
    move_to: str | None = None  # repo-relative destination for TUNED
    delete: bool = False  # repo-relative file removed (copies that became pointers)
    note: str = ""
    repo_path: str | None = None  # repo-relative current file
    extra: dict = field(default_factory=dict)  # order / derived_from carried over

    @property
    def ident(self) -> str:
        return self.file_rel or self.old_key or self.copy_key or self.after_key


@dataclass
class Plan:
    rows: list[Row] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: store -> (new JSON, [(old key, new key, [field conflicts])])
    stores: dict[str, dict] = field(default_factory=dict)
    rekeys: dict[str, list[dict]] = field(default_factory=dict)
    index_payload: dict | None = None
    index_changed: bool = False
    commit_message: str = ""
    #: the remembered last patch, when it is a copy this run removes: file, from, to
    last_patch: dict | None = None

    @property
    def file_ops(self) -> list[Row]:
        return [r for r in self.rows if r.move_to or r.delete]

    @property
    def store_changes(self) -> list[str]:
        return [label for label, spec in self.stores.items() if spec["changed"]]

    @property
    def empty(self) -> bool:
        return not self.file_ops and not self.store_changes and not self.index_changed and not self.last_patch


# --------------------------------------------------------------------- helpers


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stem(key: str) -> str:
    """The legacy stem a browser lookup falls back to (the file name without .fxp)."""
    return PurePosixPath(key.partition(":")[2]).name


def _skip_fields(label: str, klass: str) -> tuple[str, ...]:
    skip = DESCRIPTIVE_FIELDS.get(label, ())
    if klass != TUNED:
        skip = skip + CAPTURED_FIELDS.get(label, ())
    return skip


def _first_hit(container: dict, keys: list[str]) -> tuple[str | None, Any]:
    for k in keys:
        if k in container:
            return k, container[k]
    return None, None


def browser_active() -> str | None:
    """Why the touch browser counts as running, or None. A one-shot look at the
    systemd unit and at the process table."""
    unit = os.environ.get("MPE_TOUCH_UNIT", TOUCH_UNIT_DEFAULT)
    script = os.environ.get("MPE_TOUCH_SCRIPT", TOUCH_SCRIPT_DEFAULT)
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    if out in ("active", "activating", "reloading"):
        return f"{unit} is {out}"
    me = os.getpid()
    proc = Path("/proc")
    try:
        pids = [int(d.name) for d in proc.iterdir() if d.name.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        if pid == me:
            continue
        try:
            argv = (proc / str(pid) / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(os.path.basename(a.decode("utf-8", "replace")) == script for a in argv if a):
            return f"process {pid} is running {script}"
    return None


def _refuse_if_browser_active(check) -> None:
    why = (check or browser_active)()
    if why:
        raise LibraryError(
            f"the touch browser is running ({why}); stop it first. It keeps its stores in "
            "memory and would write them back over the migration. Nothing was changed."
        )


def unfinished_runs(index_path: Path) -> list[str]:
    """Stamps of runs that began changing things and neither finished nor were undone.
    A run with no manifest never got as far as changing anything (the manifest is
    written first), so it does not count."""
    base = index_path.parent / BACKUP_DIR_NAME
    out = []
    if base.is_dir():
        for d in sorted(base.glob(f"{RUN_PREFIX}*")):
            try:
                state = json.loads((d / MANIFEST).read_text(encoding="utf-8")).get("state")
            except (OSError, ValueError):
                continue
            if state not in ("done", "undone"):
                out.append(d.name[len(RUN_PREFIX):])
    return out


def _refuse_if_unfinished(index_path: Path) -> None:
    unfinished = unfinished_runs(index_path)
    if unfinished:
        stamp = unfinished[-1]
        raise LibraryError(
            f"run {stamp} began and did not finish; a new run on top of it would finish the "
            f"migration wrong. Reverse it first: undo {stamp}. Nothing was changed."
        )


def _key_for(root: Path, path: Path) -> str:
    return stable_key_for_relative_path(patch_root_label(root), path.relative_to(root))


def _library(roots: list[Path], exclude: Path) -> tuple[dict[str, list[tuple[str, Path]]], dict[str, Path]]:
    """sha256 -> [(key, path)], and key -> path, over every patch root except ``exclude``."""
    by_sha: dict[str, list[tuple[str, Path]]] = {}
    by_key: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
            here = Path(dirpath).resolve()
            if here == exclude or exclude in here.parents:
                dirs[:] = []
                continue
            for name in sorted(files):
                if not name.lower().endswith(".fxp"):
                    continue
                path = Path(dirpath) / name
                try:
                    key = stable_key_for_relative_path(
                        patch_root_label(root), path.relative_to(root)
                    )
                    digest = _sha(path)
                except (OSError, ValueError):
                    continue
                by_sha.setdefault(digest, []).append((key, path))
                by_key[key] = path
    return by_sha, by_key


def _read_store(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LibraryError(f"cannot read {path} ({exc}); nothing was changed")
    if not isinstance(raw, dict):
        raise LibraryError(f"{path} is not a JSON object; nothing was changed")
    return raw


def _tracked_clean(repo: Path, rel: str) -> str | None:
    """None when the file is tracked and unmodified, else the reason it is not."""
    if _git(repo, "ls-files", "--error-unmatch", "--", rel, check=False).returncode != 0:
        return "not tracked by git (no history to restore it from)"
    if _git(repo, "diff", "--quiet", "HEAD", "--", rel, check=False).returncode != 0:
        return "has uncommitted changes"
    return None


def _merge_entry(target: Any, copy: Any) -> tuple[Any, list[str]]:
    """The favorite's (copy's) fields over the target's. Returns (merged, conflicts)."""
    if not isinstance(target, dict) or not isinstance(copy, dict):
        return copy, ([] if target in (None, copy) else ["whole value"])
    merged = dict(target)
    conflicts = []
    for k, v in copy.items():
        if k in target and target[k] != v:
            conflicts.append(f"{k}: target {target[k]!r} -> favorite {v!r}")
        merged[k] = v
    return merged, conflicts


# ------------------------------------------------------------------------ plan


def build_plan(
    *,
    repo: Path,
    roots: list[Path],
    fav_dir: Path,
    index_path: Path,
    store_paths: dict[str, Path],
    tuned_names: tuple[str, ...] = TUNED_NAMES,
    resave_names: tuple[str, ...] = RESAVE_NAMES,
    tuned_folder: str = TUNED_FOLDER,
    last_patch_file: Path | None = None,
) -> Plan:
    """Compute everything; write nothing."""
    _refuse_if_unfinished(index_path)
    plan = Plan()
    repo = repo.resolve()
    user_root = next((r for r in roots if patch_root_label(r) == "user"), None)
    if user_root is None:
        raise LibraryError("no user patch root among the scan roots")
    real_user = user_root.resolve()
    real_fav = fav_dir.resolve() if fav_dir.exists() else fav_dir
    for label, p in (("user patch root", real_user),):
        if repo != p and repo not in p.parents:
            raise LibraryError(f"{label} {p} is not inside the library checkout {repo}")

    by_sha, by_key = _library(roots, exclude=real_fav)
    stock_by_stem: dict[str, list[str]] = {}
    for key in by_key:
        root = key.partition(":")[0]
        if root in ("factory", "thirdparty"):
            stock_by_stem.setdefault(PurePosixPath(key.partition(":")[2]).name.casefold(), []).append(key)

    index = FavoritesIndex(index_path)
    old_entries = index.entries
    fav_name = real_fav.name

    def rel_in_fav(dest: str) -> str | None:
        norm = dest.replace("\\", "/")
        marker = f"/{fav_name}/"
        at = norm.rfind(marker)
        return norm[at + len(marker) :] if at >= 0 else None

    entry_by_rel: dict[str, str] = {}
    for key, entry in old_entries.items():
        dest = entry.get("dest_path")
        rel = rel_in_fav(str(dest)) if dest else None
        if rel:
            entry_by_rel[rel] = key

    files: list[tuple[Path, str | None]] = []  # (file, rel inside the favorites folder)
    if real_fav.is_dir():
        for path in sorted(real_fav.rglob("*.fxp")):
            files.append((path, path.relative_to(real_fav).as_posix()))
    in_fav = {p for p, _ in files}
    tuned_lower = {n.casefold() for n in tuned_names}
    resave_lower = {n.casefold() for n in resave_names}
    extras: dict[str, list[Path]] = {}
    tuned_root = real_user / tuned_folder
    for path in sorted(real_user.rglob("*.fxp")):
        if path in in_fav or tuned_root in path.parents or real_fav in path.parents:
            continue
        if path.stem.casefold() in tuned_lower:
            extras.setdefault(path.stem.casefold(), []).append(path)
    for stem, paths in sorted(extras.items()):
        if len(paths) > 1:
            plan.errors.append(
                f"{stem!r} is named as tuned but found {len(paths)} times: "
                + ", ".join(str(p.relative_to(real_user)) for p in paths)
            )
        else:
            files.append((paths[0], None))

    moved_names: dict[str, str] = {}
    for path, rel in files:
        stem = path.stem
        rel_user = path.relative_to(real_user).with_suffix("").as_posix()
        copy_key = f"user:{rel_user}"
        in_tree = rel is not None
        old_key = entry_by_rel.get(rel) if rel else None
        if old_key is None and copy_key in old_entries:
            # An entry filed under the copy's own key (a copy hearted or imported while
            # the new code ran on the old index): it stands for this file. Left alone it
            # would outlive the copy and point at nothing.
            old_key = copy_key
        parent = PurePosixPath(rel).parent.as_posix() if rel else "."
        folder = "" if parent == "." else parent
        # The entry that already names the pointer's target (a second run, or a
        # favorite filed under its stock key) keeps its folder and date.
        known = old_entries.get(old_key) if old_key else None
        if known is not None:
            folder = str(known.get("folder") or folder)
        added_at = known.get("added_at") if known else None
        repo_rel = path.relative_to(repo).as_posix() if repo in path.parents else None
        try:
            digest = _sha(path)
        except OSError as exc:
            plan.errors.append(f"cannot read {path}: {exc}")
            continue
        # A file is never a copy of itself (the extras sit inside the library walk).
        matches = [(k, p) for k, p in by_sha.get(digest, []) if p.resolve() != path]
        if matches:
            klass = POINTER
            keys = sorted(k for k, _ in matches)
            preferred = [k for k in keys if k == old_key] or [
                k for k in keys if PurePosixPath(k.partition(":")[2]).name.casefold() == stem.casefold()
            ] or keys
            after_key = preferred[0]
            note = ""
            if len(keys) > 1:
                note = f"identical to {len(keys)} library files, chose {after_key}"
            row = Row(POINTER, rel, copy_key, old_key, after_key, folder, added_at, delete=True, note=note, repo_path=repo_rel)
        elif stem.casefold() in resave_lower:
            stocks = sorted(stock_by_stem.get(stem.casefold(), []))
            if len(stocks) != 1:
                plan.errors.append(
                    f"{rel or path.name}: named as a plain re-save but "
                    f"{len(stocks)} stock patches are called {stem!r} {stocks}"
                )
                continue
            row = Row(RESAVE, rel, copy_key, old_key, stocks[0], folder, added_at, delete=True,
                      note="bytes differ from stock (re-save)", repo_path=repo_rel)
        elif stem.casefold() in tuned_lower:
            dest_rel = f"{tuned_folder}/{stem}.fxp"
            dest = real_user / dest_rel
            if stem.casefold() in moved_names:
                plan.errors.append(f"two tuned files are called {stem!r}")
                continue
            moved_names[stem.casefold()] = rel or path.name
            if dest.exists():
                plan.errors.append(f"{dest_rel} already exists; not overwriting")
                continue
            row = Row(TUNED, rel, copy_key, old_key, f"user:{tuned_folder}/{stem}", folder if in_tree else "",
                      added_at, move_to=(real_user / dest_rel).relative_to(repo).as_posix() if repo in dest.parents else None,
                      repo_path=repo_rel, note="" if in_tree else "outside the favorites folder; becomes a favorite")
        else:
            row = Row(USER, rel, copy_key, old_key, copy_key, folder, added_at, repo_path=repo_rel,
                      note="own patch; stays where it is")
        if (row.delete or row.move_to) and (row.repo_path is None or (row.klass == TUNED and row.move_to is None)):
            plan.errors.append(f"{path} is outside the library checkout; cannot move it with git")
            continue
        if row.delete or row.move_to:
            why = _tracked_clean(repo, row.repo_path)  # type: ignore[arg-type]
            if why:
                plan.errors.append(f"{row.repo_path}: {why}; refusing (it could not be restored)")
        plan.rows.append(row)

    # Index entries no file explains stay as pointers.
    explained = {r.old_key for r in plan.rows if r.old_key}
    for key, entry in old_entries.items():
        if key in explained:
            continue
        plan.rows.append(
            Row(INDEX_ONLY, None, None, key, normalize_key(key), str(entry.get("folder") or ""),
                entry.get("added_at"),
                extra={k: entry[k] for k in ("order", "derived_from") if k in entry})
        )

    for row in plan.rows:
        known = old_entries.get(row.after_key)
        if known is not None and row.klass in (USER, TUNED, POINTER, RESAVE):
            row.added_at = row.added_at or known.get("added_at")
            row.folder = str(known.get("folder") or row.folder)
            row.extra = {k: known[k] for k in ("order", "derived_from") if k in known}

    # Two copies of one patch collapse to one favorite.
    seen: dict[str, Row] = {}
    for row in plan.rows:
        first = seen.get(row.after_key)
        if first is not None:
            row.note = (row.note + "; " if row.note else "") + f"same favorite as {first.file_rel or first.old_key}"
        else:
            seen[row.after_key] = row

    # Settings. The favorite's value is the first hit the browser would have read for
    # the copy (its key, then its legacy stem), laid over what the target already has.
    for label in REKEY_STORES:
        path = store_paths.get(label)
        raw = _read_store(path) if path else None
        if raw is None:
            continue
        container = dict(_key_container(raw, label))
        changes: list[dict] = []
        for row in plan.rows:
            if row.copy_key is None or row.copy_key == row.after_key or row.klass == USER:
                continue
            skip = _skip_fields(label, row.klass)
            hit_key, moved = _first_hit(container, [row.copy_key, _stem(row.copy_key)])
            if hit_key == row.copy_key:
                container.pop(row.copy_key)  # a stem row is shared; it stays where it is
            elif moved is not None:
                moved = json.loads(json.dumps(moved))
            base = container.get(row.after_key)
            stem_hit = hit_key is not None and hit_key != row.copy_key
            tuned_base = False
            if row.klass == TUNED and base is None and row.old_key and row.old_key in container:
                # A tuned patch began as the stock patch it was filed under: it starts
                # from that patch's settings (copied; the stock patch keeps its own).
                base = json.loads(json.dumps(container[row.old_key]))
                if isinstance(base, dict):
                    for f in DESCRIPTIVE_FIELDS.get(label, ()):
                        base.pop(f, None)
                tuned_base = True
            if moved is None and base is None:
                continue
            if isinstance(moved, dict):
                moved = {k: v for k, v in moved.items() if k not in skip}
            merged, conflicts = _merge_entry(base, moved) if moved is not None else (base, [])
            if not (isinstance(merged, dict) and not merged and base is None):
                container[row.after_key] = merged
            src = hit_key if hit_key is not None else row.old_key
            changes.append({"from": src, "to": row.after_key, "conflicts": conflicts,
                            "copied": stem_hit or (tuned_base and moved is None)})
        plan.rekeys[label] = changes
        new_raw = dict(raw)
        sub = {"metadata": "patches"}.get(label)
        if sub is None:
            new_raw = container
        else:
            new_raw[sub] = container
        plan.stores[label] = {"path": str(path), "new": new_raw, "changed": bool(changes) and new_raw != raw}

    # The patch the browser reopens: a removed copy would leave it pointing at nothing.
    if last_patch_file is not None and Path(last_patch_file).exists():
        try:
            state = json.loads(Path(last_patch_file).read_text(encoding="utf-8"))
            remembered = Path(str(state.get("patch_path") or ""))
        except (OSError, ValueError, AttributeError):
            remembered = None
        if remembered is not None and str(remembered):
            try:
                real_remembered = remembered.resolve()
            except OSError:
                real_remembered = remembered
            for row in plan.rows:
                if row.klass not in (POINTER, RESAVE, TUNED) or not row.repo_path:
                    continue
                if (repo / row.repo_path).resolve() != real_remembered:
                    continue
                if row.klass == TUNED:
                    target = user_root / f"{row.after_key.partition(':')[2]}.fxp"
                else:
                    target = by_key.get(row.after_key)
                if target is not None:
                    plan.last_patch = {"file": str(last_patch_file), "from": str(remembered), "to": str(target)}
                break

    # Index.
    created_at = datetime.now(timezone.utc).isoformat()
    new_index = FavoritesIndex(index_path)
    new_index.upgrade()
    for key in list(new_index.entries):
        new_index.remove(key)
    for row in plan.rows:
        if row.after_key in new_index.entries:
            continue
        new_index.add(
            row.after_key,
            folder=row.folder,
            added_at=row.added_at or created_at,
            order=row.extra.get("order"),
            derived_from=row.extra.get("derived_from"),
        )
        if row.klass == USER:
            new_index.mark_imported(row.after_key)
    payload = new_index.payload()
    plan.index_payload = payload
    current = index_path.read_text(encoding="utf-8") if index_path.exists() else None
    try:
        same = current is not None and json.loads(current) == payload
    except ValueError:
        same = False
    plan.index_changed = not same
    counts = {k: sum(1 for r in plan.rows if r.klass == k) for k in (POINTER, RESAVE, TUNED, USER, INDEX_ONLY)}
    plan.commit_message = (
        "Favorites become pointers (#181): "
        f"{counts[POINTER] + counts[RESAVE]} copies removed, "
        f"{counts[TUNED]} tuned patches moved to {tuned_folder}/"
    )
    return plan


def format_plan(plan: Plan) -> str:
    lines: list[str] = []
    counts: dict[str, int] = {}
    for r in plan.rows:
        counts[r.klass] = counts.get(r.klass, 0) + 1
    lines.append("favorites: " + (", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "none"))
    for r in sorted(plan.rows, key=lambda x: (x.klass, x.file_rel or x.old_key or "")):
        src = r.file_rel or r.repo_path or f"(index) {r.old_key}"
        extra = f"  [{r.note}]" if r.note else ""
        folder = f"  folder={r.folder!r}" if r.folder else ""
        lines.append(f"  {r.klass:15s} {src} -> {r.after_key}{folder}{extra}")
    for label, changes in plan.rekeys.items():
        if not changes:
            continue
        lines.append(f"{label}: {len(changes)} key(s) re-keyed")
        for c in changes:
            tag = "copy" if c["copied"] else "move"
            lines.append(f"  {tag} {c['from']} -> {c['to']}")
            for conflict in c["conflicts"]:
                lines.append(f"    CONFLICT (favorite wins) {conflict}")
    for r in plan.file_ops:
        if r.move_to:
            lines.append(f"git mv {r.repo_path} {r.move_to}")
        else:
            lines.append(f"git rm {r.repo_path}")
    if plan.file_ops:
        lines.append(f"git commit -m {plan.commit_message!r}")
    lines.append(f"index: {'rewritten as version 2' if plan.index_changed else 'unchanged'}")
    for n in plan.notes:
        lines.append(f"note: {n}")
    for e in plan.errors:
        lines.append(f"ERROR: {e}")
    if plan.empty and not plan.errors:
        lines.append("nothing to do: already migrated")
    return "\n".join(lines)


# ----------------------------------------------------------------- snapshot/apply


def _hits(stores: dict[str, dict | None], keys: list[str]) -> dict[str, dict | None]:
    """First hit over ``keys`` in each store, in the order given (the browser's order)."""
    out: dict[str, dict | None] = {}
    for label, raw in stores.items():
        if raw is None:
            out[label] = None
            continue
        _k, found = _first_hit(_key_container(raw, label), keys)
        out[label] = found if isinstance(found, dict) else ({} if found is not None else None)
    return out


def _before_keys(r: Row) -> list[str]:
    """What the browser read for this favorite before: the copy's key, then its stem
    (or, with no copy, the key the index filed it under, then that stem)."""
    first = r.copy_key or r.old_key or r.after_key
    return [first, _stem(first)]


def _after_keys(key: str) -> list[str]:
    return [key, _stem(key)]


def _before_snapshot(plan: Plan, store_paths: dict[str, Path]) -> dict:
    raws = {label: _read_store(store_paths[label]) if label in store_paths else None for label in REKEY_STORES}
    favs = []
    for r in plan.rows:
        effective = _hits(raws, _before_keys(r))
        any_hit = _hits(raws, [k for k in (r.copy_key, r.old_key, normalize_key(r.old_key) if r.old_key else None) if k])
        def carries(label: str, v: dict | None) -> bool:
            if v is None:
                return False
            return any(k not in _skip_fields(label, r.klass) for k in v) if v else True

        favs.append({
            "id": r.ident,
            "klass": r.klass,
            "copy_key": r.copy_key,
            "old_key": r.old_key,
            "after_key": r.after_key,
            "hit": {label: carries(label, any_hit[label]) or carries(label, effective[label]) for label in raws},
            "effective": {label: v for label, v in effective.items() if v},
            "skip": {label: list(_skip_fields(label, r.klass)) for label in raws if _skip_fields(label, r.klass)},
        })
    return {"version": 2, "favorites": favs}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def run_dir_for(index_path: Path, stamp: str) -> Path:
    return index_path.parent / BACKUP_DIR_NAME / f"{RUN_PREFIX}{stamp}"


def latest_run(index_path: Path) -> str | None:
    """The newest run in any state (a started one counts: it is the one to look at)."""
    base = index_path.parent / BACKUP_DIR_NAME
    if not base.is_dir():
        return None
    runs = [d.name[len(RUN_PREFIX):] for d in sorted(base.glob(f"{RUN_PREFIX}*")) if (d / MANIFEST).exists()]
    return runs[-1] if runs else None


def _file_sha(path: Path) -> str | None:
    try:
        return _sha(path) if path.is_file() else None
    except OSError:
        return None


def apply_plan(
    plan: Plan,
    *,
    repo: Path,
    index_path: Path,
    store_paths: dict[str, Path],
    stamp: str | None = None,
    browser_check=None,
) -> str | None:
    """Back up, move files in git, write stores and index. Returns the run stamp
    (None when there was nothing to do)."""
    if plan.errors:
        raise LibraryError("the plan has errors; nothing was changed:\n  " + "\n  ".join(plan.errors))
    _refuse_if_unfinished(index_path)
    if plan.empty:
        return None
    _refuse_if_browser_active(browser_check)
    repo = repo.resolve()
    staged = _git(repo, "diff", "--cached", "--name-only").stdout.split()
    if staged:
        raise LibraryError("the library checkout has staged changes; commit or unstage them first: " + ", ".join(staged[:5]))
    stamp = stamp or _stamp()
    run = run_dir_for(index_path, stamp)
    if run.exists():
        raise LibraryError(f"{run} already exists; nothing was changed")
    backup = run / "backup"
    backup.mkdir(parents=True)
    backup_sha: dict[str, str] = {}
    for label, path in sorted(store_paths.items()):
        if path.exists():
            shutil.copy2(path, backup / f"{label}.json")
            backup_sha[label] = _sha(path)
    if index_path.exists():
        shutil.copy2(index_path, backup / "index.json")
        backup_sha["index"] = _sha(index_path)
    if plan.last_patch:
        lp_file = Path(plan.last_patch["file"])
        if lp_file.exists():
            shutil.copy2(lp_file, backup / "last_patch.json")
    base_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (run / BEFORE).write_text(
        json.dumps(_before_snapshot(plan, store_paths), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "version": 2,
        "state": "started",
        "stamp": stamp,
        "repo_base_commit": base_commit,
        "commit": None,
        "message": plan.commit_message,
        "stores": {label: str(p) for label, p in store_paths.items()},
        "index": str(index_path),
        # Everything undo needs to reverse the git step is here before git is touched.
        "file_ops": [
            {"klass": r.klass, "from": r.repo_path, "to": r.move_to,
             "sha": _file_sha(repo / r.repo_path) if r.repo_path else None}
            for r in plan.file_ops
        ],
        "backup_sha": backup_sha,
        "written_sha": {},
        "writing": None,
        "last_patch": {"file": plan.last_patch["file"]} if plan.last_patch else None,
        "favorites": [
            {"id": r.ident, "klass": r.klass, "copy_key": r.copy_key,
             "old_key": r.old_key, "after_key": r.after_key}
            for r in plan.rows
        ],
    }

    def save_manifest() -> None:
        atomic_write_json(run / MANIFEST, manifest)

    save_manifest()
    if plan.file_ops:
        for r in plan.file_ops:
            if r.move_to:
                (repo / r.move_to).parent.mkdir(parents=True, exist_ok=True)
                _git(repo, "mv", "--", r.repo_path, r.move_to)  # type: ignore[arg-type]
            else:
                _git(repo, "rm", "-q", "--", r.repo_path)  # type: ignore[arg-type]
        _git(repo, "commit", "-q", "-m", plan.commit_message)
        manifest["commit"] = _git(repo, "rev-parse", "HEAD").stdout.strip()
        save_manifest()

    def write(label: str, path: Path, data: dict) -> None:
        manifest["writing"] = label  # undo accepts either state of a file caught mid-write
        save_manifest()
        atomic_write_json(path, data)
        manifest["written_sha"][label] = _sha(path)
        manifest["writing"] = None
        save_manifest()

    for label, spec in plan.stores.items():
        if spec["changed"]:
            write(label, Path(spec["path"]), spec["new"])
    if plan.index_changed and plan.index_payload is not None:
        write("index", index_path, plan.index_payload)
    if plan.last_patch:
        lp_file = Path(plan.last_patch["file"])
        try:
            state = json.loads(lp_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state["patch_path"] = plan.last_patch["to"]
        write("last_patch", lp_file, state)
    manifest["state"] = "done"
    save_manifest()
    return stamp


# ------------------------------------------------------------------------ verify


def verify(*, index_path: Path, store_paths: dict[str, Path], roots: list[Path], stamp: str) -> list[str]:
    """Problems found comparing the run's before snapshot with the stores now. Empty = pass.

    For every favorite that existed before: it is still a favorite, its target is
    on disk (index-only favorites included), every store that had a hit for it still
    has one, and the values the browser read for it before (first hit over its keys, in
    the browser's order) are what the browser reads for it now.
    """
    run = run_dir_for(index_path, stamp)
    try:
        before = json.loads((run / BEFORE).read_text(encoding="utf-8"))
        manifest = json.loads((run / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read run {stamp}: {exc}"]
    problems: list[str] = []
    state = manifest.get("state")
    if state == "undone":
        problems.append(f"run {stamp} was undone; there is nothing of it to verify")
    elif state != "done":
        problems.append(f"run {stamp} did not finish (state {state!r}); undo {stamp}")
    index = FavoritesIndex(index_path)
    if index.legacy:
        problems.append("the index is still version 1")
    raws = {label: _read_store(store_paths[label]) if label in store_paths else None for label in REKEY_STORES}
    keys: set[str] = set()
    for root in roots:
        if root.is_dir():
            for dirpath, _d, files in os.walk(root):
                for f in files:
                    if f.lower().endswith(".fxp"):
                        keys.add(_key_for(root, Path(dirpath) / f))
    for fav in before["favorites"]:
        ident = fav["id"]
        after = fav["after_key"]
        if not index.is_favorited(after):
            problems.append(f"{ident}: no longer a favorite (expected {after})")
        if after not in keys:
            problems.append(f"{ident}: target {after} is not in the library")
        old = fav.get("old_key")
        # Only a re-spelling of the same key counts as the same hit.
        candidates = [after] + ([old] if old and old != after and normalize_key(old) == after else [])
        now = _hits(raws, candidates + [_stem(after)])
        for label, was in fav["hit"].items():
            if was and now.get(label) is None:
                problems.append(f"{ident}: {label} hit dropped (had one before, none under {after})")
        skip = fav.get("skip", {})
        for label, values in fav.get("effective", {}).items():
            have = now.get(label) or {}
            if isinstance(values, dict):
                for k, v in values.items():
                    if k in skip.get(label, ()):
                        continue
                    if have.get(k) != v:
                        problems.append(f"{ident}: {label}.{k} was {v!r} on the favorite, now {have.get(k)!r}")
    return problems


# -------------------------------------------------------------------------- undo


def _undo_refusals(manifest: dict, repo: Path, index_path: Path, force: bool) -> tuple[list[str], list[str]]:
    """(hard refusals, refusals that --force overrides). Nothing is changed here."""
    hard: list[str] = []
    soft: list[str] = []
    for op in manifest.get("file_ops") or []:
        src, dst, sha = op.get("from"), op.get("to"), op.get("sha")
        if src and (repo / src).exists():
            tracked_here = _file_sha(repo / src) == sha
            if not tracked_here:
                hard.append(
                    f"{src} exists and is not the file this run moved (it was saved or edited "
                    "since); move it aside, then undo"
                )
        if dst and (repo / dst).exists() and _file_sha(repo / dst) != sha:
            hard.append(f"{dst} was edited since the migration; not undoing over it")
    backup_sha = manifest.get("backup_sha") or {}
    written = manifest.get("written_sha") or {}
    writing = manifest.get("writing")
    targets = {label: Path(p) for label, p in (manifest.get("stores") or {}).items()}
    targets["index"] = index_path
    for label, target in sorted(targets.items()):
        if label == "favorites" or label == writing:
            continue
        now = _file_sha(target)
        allowed = {backup_sha.get(label), written.get(label)}
        if now not in allowed:
            if now is None:
                continue  # gone; the restore puts it back
            soft.append(
                f"{label} ({target}) changed since the run: it is neither what the run found "
                "nor what the run wrote, so undoing would discard settings made after it"
            )
    return hard, soft


def undo(*, repo: Path, index_path: Path, stamp: str, force: bool = False, browser_check=None) -> list[str]:
    """Put back the stores and index from the run's backup and reverse its git moves."""
    run = run_dir_for(index_path, stamp)
    try:
        manifest = json.loads((run / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LibraryError(f"cannot read run {stamp}: {exc}")
    if manifest.get("state") == "undone":
        return [f"run {stamp} was already undone; nothing to do"]
    repo = repo.resolve()
    _refuse_if_browser_active(browser_check)
    hard, soft = _undo_refusals(manifest, repo, index_path, force)
    if hard or (soft and not force):
        lines = hard + soft
        tail = "" if hard or not soft else "; pass --force to restore anyway"
        raise LibraryError("not undoing run " + stamp + ":\n  " + "\n  ".join(lines) + tail + "\nNothing was changed.")
    notes: list[str] = [f"--force: {m}" for m in soft]
    ops = manifest.get("file_ops") or []
    if ops:
        # Whether the commit happened or not: put the op paths back to the base commit.
        base = manifest["repo_base_commit"]
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        for op in ops:
            if op["to"]:
                _git(repo, "rm", "-q", "-f", "--ignore-unmatch", "--", op["to"])
                (repo / op["to"]).unlink(missing_ok=True)  # a move that stopped half way
            _git(repo, "checkout", base, "--", op["from"])
        if _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
            _git(repo, "commit", "-q", "-m", f"Undo favorites pointers migration {stamp}")
            notes.append(f"git: moves reversed in {_git(repo, 'rev-parse', '--short', 'HEAD').stdout.strip()} (was {head[:10]})")
        else:
            notes.append("git: working tree and index are back at the base commit (nothing to commit)")
    backup = run / "backup"
    for label, path in manifest["stores"].items():
        src = backup / f"{label}.json"
        target = Path(path)
        if src.exists():
            tmp = target.with_name(f".{target.name}.undo.tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, target)
            notes.append(f"restored {label}: {target}")
        elif target.exists() and label != "favorites":
            notes.append(f"{label}: no backup (it did not exist); left as is")
    idx_backup = backup / "index.json"
    if idx_backup.exists():
        tmp = index_path.with_name(f".{index_path.name}.undo.tmp")
        shutil.copy2(idx_backup, tmp)
        os.replace(tmp, index_path)
        notes.append(f"restored index: {index_path}")
    elif index_path.exists():
        index_path.unlink()
        notes.append("removed index (there was none before)")
    lp = manifest.get("last_patch")
    if lp:
        lp_file = Path(lp["file"])
        lp_backup = backup / "last_patch.json"
        written_lp = (manifest.get("written_sha") or {}).get("last_patch")
        if lp_backup.exists() and _file_sha(lp_file) == written_lp:
            shutil.copy2(lp_backup, lp_file)
            notes.append(f"restored last patch: {lp_file}")
        else:
            notes.append("last patch changed since the run; left as is")
    manifest["state"] = "undone"
    atomic_write_json(run / MANIFEST, manifest)
    notes.append("settings changed after the migration are gone with the restore")
    return notes


# ------------------------------------------------------------------- environment


def environment(repo: str | None) -> dict:
    """Paths from the device environment (nothing here is committed anywhere)."""
    repo_s = repo or os.environ.get("MPE_PERSONAL_REPO", "")
    if not repo_s:
        raise LibraryError("no library checkout: pass --repo or set MPE_PERSONAL_REPO")
    repo_p = Path(repo_s).expanduser()
    if not (repo_p / ".git").exists():
        raise LibraryError(f"{repo_p} is not a git checkout")
    from patch_browser.favorites_index import default_favorites_index_path
    from patch_browser.patch_scanner import FAVORITES_NAME, LAST_PATCH_FILE

    roots = default_scan_roots()
    user = next((r for r in roots if patch_root_label(r) == "user"), None)
    if user is None:
        raise LibraryError("no user patch root")
    stores = default_store_paths()
    stores.pop("favorites", None)
    return {
        "repo": repo_p.resolve(),
        "roots": roots,
        "fav_dir": user / FAVORITES_NAME.lstrip("!"),
        "index_path": default_favorites_index_path(),
        "store_paths": stores,
        "last_patch_file": LAST_PATCH_FILE,
    }
