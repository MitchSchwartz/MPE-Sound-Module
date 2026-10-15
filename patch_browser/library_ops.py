"""Library git operations: restore to default, template merge, store-hit check.

The personal library is a git checkout whose ``upstream`` remote is the clean
template. Everything here is plain git against those two remotes, so the same
code runs on the laptop today and on the device later.

Safety order, for every mode that can lose work:

1. a backup commit of what is about to change,
2. a ``pre-restore/<UTC timestamp>`` tag on that commit,
3. a push of the branch and the tag (never forced),
4. only then the working tree is touched.

If the push fails the operation stops before step 4, unless the caller passed
``allow_offline`` (the commit and the tag still exist locally).

Run with the patch browser stopped when the settings copies are applied to the
device afterwards: the browser holds its stores in memory.

Config (environment, none of it committed anywhere):
``MPE_PERSONAL_REPO`` the personal checkout (or ``--repo``),
``MPE_LIBRARY_ORIGIN_REMOTE`` default ``origin``,
``MPE_LIBRARY_UPSTREAM_REMOTE`` default ``upstream``,
``MPE_LIBRARY_UPSTREAM_REF`` default ``main``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# Where each stable-key root lives inside the library, relative to its root.
# The root label comes from the *name* of the directory Surge scans
# (patch_identity.patch_root_label), so the link names on the device must not
# change; the repo-side layout is what these paths describe.
PATCH_ROOTS: dict[str, str] = {
    "factory": "assets/patches/patches_factory",
    "thirdparty": "assets/patches/third-party/patches_3rdparty",
    "user": "assets/user-data/Patches",
}
USER_PATCHES = PATCH_ROOTS["user"]

# Children of assets/ that are not stock content: restore-stock never touches them.
NON_STOCK_ASSET_DIRS = frozenset({"user-data", "binaries", "appliance-state"})

SETTINGS_DIR = "settings"
DEFAULTS_DIR = "defaults"
KEPT_FILE = ".library/kept-template-versions.json"
KEPT_VERSION = 1
TAG_PREFIX = "pre-restore/"

# Per-patch stores, by short name -> file name under settings/.
STORE_FILES: dict[str, str] = {
    "fx": "patch_browser_fx.json",
    "hold": "patch_browser_hold.json",
    "pressure": "patch_browser_pressure.json",
    "normalization": "patch_browser_normalization.json",
    "metadata": "patch_browser_metadata.json",
}
# Stores that factory reset puts back to the template's defaults/<name>.json.
DEFAULTS_FILES: dict[str, str] = {
    "normalization": "normalization.json",
    "pressure": "pressure.json",
    "metadata": "metadata.json",
    "favorites": "favorites.json",
}
FAVORITES_SETTINGS_FILE = "patch_browser_favorites.json"


class LibraryError(RuntimeError):
    """A refusal or a failed git step; the message says what state was left."""


@dataclass
class Config:
    repo: Path
    origin: str = "origin"
    upstream: str = "upstream"
    ref: str = "main"
    fetch: bool = True
    push: bool = True
    allow_offline: bool = False
    #: Home directory of the live device (where ~/.patch_browser_*.json live).
    #: When set, restore snapshots it into settings/ before the backup commit.
    live_home: Path | None = None
    device_id: str | None = None

    @property
    def upstream_ref(self) -> str:
        return f"{self.upstream}/{self.ref}"


@dataclass
class Result:
    action: str
    backup_commit: str | None = None
    tag: str | None = None
    pushed: bool = False
    notes: list[str] = field(default_factory=list)

    def say(self, text: str) -> None:
        self.notes.append(text)


# --------------------------------------------------------------------------- git


def _git(cfg_or_repo, *args: str, check: bool = True, input_text: str | None = None):
    repo = cfg_or_repo.repo if isinstance(cfg_or_repo, Config) else cfg_or_repo
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise LibraryError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc


def _out(cfg, *args: str) -> str:
    return _git(cfg, *args).stdout.strip()


def _utc_stamp(now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _has_remote(cfg: Config, name: str) -> bool:
    return name in _out(cfg, "remote").split()


def _ref_exists(cfg: Config, ref: str) -> bool:
    return _git(cfg, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0


def _path_in_ref(cfg: Config, ref: str, path: str) -> bool:
    return _git(cfg, "cat-file", "-e", f"{ref}:{path}", check=False).returncode == 0


def _tracked_or_present(cfg: Config, path: str) -> bool:
    if (cfg.repo / path).exists():
        return True
    return bool(_out(cfg, "ls-files", "--", path))


def fetch_upstream(cfg: Config, result: Result) -> None:
    """Refresh the template ref. Offline: warn and use what was last fetched."""
    if not _has_remote(cfg, cfg.upstream):
        raise LibraryError(f"no '{cfg.upstream}' remote in {cfg.repo}; add the template as upstream")
    if cfg.fetch:
        proc = _git(cfg, "fetch", cfg.upstream, check=False)
        if proc.returncode != 0:
            result.say(f"fetch failed, using the last fetched {cfg.upstream_ref}")
    if not _ref_exists(cfg, cfg.upstream_ref):
        raise LibraryError(f"{cfg.upstream_ref} is not available; fetch the template once while online")


def _under(path: str, bases: list[str]) -> bool:
    return any(path == b or path.startswith(b.rstrip("/") + "/") for b in bases)


def _require_clean_index(cfg: Config, allowed: list[str] | None = None) -> None:
    """Staged changes are fine inside the paths about to be backed up (a restore leaves
    its result staged); anything staged elsewhere would be swept into the commit."""
    staged = [p for p in _git(cfg, "diff", "--cached", "--name-only", "-z").stdout.split("\0") if p]
    stray = [p for p in staged if not _under(p, allowed or [])]
    if stray:
        raise LibraryError(f"the index has staged changes outside the backed-up paths (first: {stray[0]}); commit or unstage them first")


def snapshot_live_settings(cfg: Config, result: Result) -> None:
    """Copy the live stores into settings/ so the backup holds what the device has now.

    settings/ is only as fresh as the last write-back. Without this step a restore
    would back up a stale copy, and applying settings/ afterwards would overwrite
    newer live edits.
    """
    if cfg.live_home is None:
        return
    script = Path(__file__).resolve().parents[1] / "scripts" / "library-snapshot-settings.sh"
    cmd = ["bash", str(script), "--from", str(cfg.live_home), "--repo", str(cfg.repo)]
    if cfg.device_id:
        cmd += ["--device-id", cfg.device_id]
    env = dict(os.environ)
    own_home = Path(os.environ.get("MPE_HOME") or Path.home())
    try:
        is_own = cfg.live_home.resolve() == own_home.resolve()
    except OSError:
        is_own = False
    if not is_own:
        # Another machine's home: this machine's /etc/mpe/mpe.env (and its MPE_*_FILE
        # overrides) say nothing about it. On the device's own home they apply.
        env["MPE_ENV_FILE"] = ""
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise LibraryError(
            "could not snapshot the live settings before restoring; nothing was changed: "
            + (proc.stderr or proc.stdout).strip()
        )
    result.say(f"live settings from {cfg.live_home} copied into settings/ first")


PATHS_TRAILER = "Backup-Paths:"


def backup_and_tag(cfg: Config, paths: list[str], message: str, result: Result, *, now=None) -> None:
    """Steps 1 to 3 of the safety order. Raises before anything is restored."""
    _require_clean_index(cfg, paths)
    message = message + "\n\n" + "\n".join(f"{PATHS_TRAILER} {p}" for p in paths)
    present = [p for p in paths if _tracked_or_present(cfg, p)]
    if present:
        _git(cfg, "add", "-A", "--", *present)
    # --allow-empty: there is always a commit to tag, so the undo point exists
    # even when nothing changed since the last commit.
    _git(cfg, "commit", "--allow-empty", "-q", "-m", message)
    result.backup_commit = _out(cfg, "rev-parse", "HEAD")

    base = f"{TAG_PREFIX}{_utc_stamp(now)}"
    tag, n = base, 1
    while _git(cfg, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).returncode == 0:
        n += 1
        tag = f"{base}-{n}"
    _git(cfg, "tag", tag, result.backup_commit)
    result.tag = tag

    if not cfg.push:
        result.say("push skipped by request; the backup exists locally only")
        return
    failure = _push_branch_and_tag(cfg, tag)
    if failure is None:
        result.pushed = True
        return
    if cfg.allow_offline:
        result.say(f"push failed ({failure}); backup commit and tag are local only")
        return
    raise LibraryError(
        f"backup commit {result.backup_commit[:10]} and tag {tag} were made but the push failed "
        f"({failure}). Nothing was restored. Fix the connection or pass --allow-offline."
    )


def _push_branch_and_tag(cfg: Config, tag: str) -> str | None:
    if not _has_remote(cfg, cfg.origin):
        return f"no '{cfg.origin}' remote"
    for refspec in ("HEAD", f"refs/tags/{tag}"):
        proc = _git(cfg, "push", cfg.origin, refspec, check=False)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout).strip() else "push error"
    return None


# --------------------------------------------------------------------- patch paths


def patch_path_for_key(stable_key: str) -> str:
    """``factory:Bass/Lead 1`` -> ``assets/patches/patches_factory/Bass/Lead 1.fxp``."""
    root, sep, rel = stable_key.partition(":")
    if not sep or not rel or root not in PATCH_ROOTS:
        raise LibraryError(f"not a stable key (expected one of {sorted(PATCH_ROOTS)}:<path>): {stable_key!r}")
    parts = PurePosixPath(rel).parts
    if PurePosixPath(rel).is_absolute() or ".." in parts or "" in parts:
        raise LibraryError(f"unsafe path in stable key: {stable_key!r}")
    return f"{PATCH_ROOTS[root]}/{rel}.fxp"


# ------------------------------------------------------------------ store dropping


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def drop_patch_settings(settings_dir: Path, stable_key: str) -> list[str]:
    """Remove one patch's user settings from the settings snapshot.

    Goes through the store classes (never raw JSON edits), only touches stores
    whose file exists, and clears the *user* part of an entry: calibrated
    baselines stay, since those are the defaults. Returns the store names whose
    file changed.
    """
    from patch_browser.patch_fx import PatchFx, PatchFxStore
    from patch_browser.patch_hold import PatchHoldStore
    from patch_browser.patch_metadata import PatchMetadataIndex
    from patch_browser.patch_normalization import PatchNormalizationStore
    from patch_browser.patch_pressure import PatchPressureStore

    name = PurePosixPath(stable_key.partition(":")[2]).name
    kw = {"stable_key": stable_key}
    changed: list[str] = []

    def _run(label: str, fn) -> None:
        path = settings_dir / STORE_FILES[label]
        if not path.exists():
            return
        before = _read_bytes(path)
        fn(path)
        if _read_bytes(path) != before:
            changed.append(label)

    _run("fx", lambda p: PatchFxStore(p).set(name, PatchFx(), **kw))
    _run("hold", lambda p: PatchHoldStore(p).clear_user_hold_mult(name, **kw))
    _run("pressure", lambda p: PatchPressureStore(p).clear_user_touch_offset(name, **kw))
    _run("normalization", lambda p: PatchNormalizationStore(p).clear_user_trim_db(name, **kw))

    def _metadata(p: Path) -> None:
        index = PatchMetadataIndex(user_path=p)
        if index.user_row(stable_key) is not None:
            index.restore_user_row(stable_key, None)

    _run("metadata", _metadata)
    return changed


def reset_settings_to_defaults(cfg: Config, settings_dir: Path, result: Result) -> list[str]:
    """Factory reset of the per-patch stores: template defaults/, else empty."""
    from patch_browser.patch_metadata import METADATA_VERSION

    settings_dir.mkdir(parents=True, exist_ok=True)
    touched: list[str] = []

    def _default_bytes(default_name: str) -> bytes | None:
        proc = _git(cfg, "show", f"{cfg.upstream_ref}:{DEFAULTS_DIR}/{default_name}", check=False)
        return proc.stdout.encode("utf-8") if proc.returncode == 0 else None

    empties: dict[str, bytes] = {
        "metadata": (json.dumps({"version": METADATA_VERSION, "patches": {}}, indent=2) + "\n").encode(),
    }
    for label, file_name in STORE_FILES.items():
        data = _default_bytes(DEFAULTS_FILES[label]) if label in DEFAULTS_FILES else None
        if data is None:
            data = empties.get(label, b"{}\n")
        _write_replace(settings_dir / file_name, data)
        touched.append(file_name)
    fav = _default_bytes(DEFAULTS_FILES["favorites"])
    if fav is not None:
        _write_replace(settings_dir / FAVORITES_SETTINGS_FILE, fav)
        touched.append(FAVORITES_SETTINGS_FILE)
    else:
        result.say("no defaults/favorites.json in the template: favorites index left as it was")
    return touched


def _write_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# -------------------------------------------------------------------- restore modes


def restore_patch(cfg: Config, stable_key: str, *, now=None) -> Result:
    result = Result("restore-patch")
    rel = patch_path_for_key(stable_key)
    fetch_upstream(cfg, result)
    if not _path_in_ref(cfg, cfg.upstream_ref, rel):
        raise LibraryError(f"the template has no version of {rel}; nothing to restore")
    snapshot_live_settings(cfg, result)
    backup_and_tag(cfg, [rel, SETTINGS_DIR], f"backup before restoring {stable_key}", result, now=now)
    _git(cfg, "checkout", cfg.upstream_ref, "--", rel)
    dropped = drop_patch_settings(cfg.repo / SETTINGS_DIR, stable_key)
    result.say(f"restored {rel} from {cfg.upstream_ref}")
    result.say("cleared settings in: " + (", ".join(dropped) if dropped else "(none had an entry)"))
    return result


def stock_asset_dirs(cfg: Config) -> list[str]:
    """Stock directories under assets/ in the template: patches plus CC0 collections."""
    names = _out(cfg, "ls-tree", "-d", "--name-only", cfg.upstream_ref, "assets/").splitlines()
    return sorted(n for n in names if PurePosixPath(n).name not in NON_STOCK_ASSET_DIRS)


def restore_stock(cfg: Config, *, now=None) -> Result:
    result = Result("restore-stock")
    fetch_upstream(cfg, result)
    dirs = stock_asset_dirs(cfg)
    if not dirs:
        raise LibraryError(f"{cfg.upstream_ref} has no stock directories under assets/")
    backup_and_tag(cfg, [*dirs, SETTINGS_DIR], "backup before restoring stock patches", result, now=now)
    _git(cfg, "checkout", cfg.upstream_ref, "--", *dirs)
    result.say("restored from template: " + ", ".join(dirs))
    result.say("your own patches and settings were not touched")
    return result


def factory_reset(cfg: Config, *, now=None) -> Result:
    result = Result("factory-reset")
    fetch_upstream(cfg, result)
    dirs = stock_asset_dirs(cfg)
    snapshot_live_settings(cfg, result)
    backup_and_tag(
        cfg,
        [*dirs, USER_PATCHES, SETTINGS_DIR],
        "backup before factory reset",
        result,
        now=now,
    )
    if dirs:
        _git(cfg, "checkout", cfg.upstream_ref, "--", *dirs)
    # User patches go back to the template's starter set (maybe none).
    _git(cfg, "rm", "-r", "-q", "--ignore-unmatch", "--", USER_PATCHES)
    if _path_in_ref(cfg, cfg.upstream_ref, USER_PATCHES):
        _git(cfg, "checkout", cfg.upstream_ref, "--", USER_PATCHES)
    # The device's link points here; it must exist (maybe empty) at every moment after.
    (cfg.repo / USER_PATCHES).mkdir(parents=True, exist_ok=True)
    touched = reset_settings_to_defaults(cfg, cfg.repo / SETTINGS_DIR, result)
    result.say("stock patches and user patches reset to the template")
    result.say("settings reset: " + ", ".join(touched))
    return result


def undo_restore(cfg: Config, tag: str, *, force: bool = False, now=None) -> Result:
    """Put back what a restore replaced: only the paths that backup covered.

    Never ``checkout <tag> -- .``: that would also wipe uncommitted edits elsewhere
    in the checkout. Refuses if tracked files outside the backed-up paths have
    uncommitted changes (unless force).
    """
    result = Result("undo")
    if not tag.startswith(TAG_PREFIX) or not _ref_exists(cfg, f"refs/tags/{tag}"):
        raise LibraryError(f"{tag!r} is not a {TAG_PREFIX}* tag in this checkout")
    body = _out(cfg, "log", "-1", "--format=%B", f"refs/tags/{tag}^{{commit}}")
    paths = [ln[len(PATHS_TRAILER):].strip() for ln in body.splitlines() if ln.startswith(PATHS_TRAILER)]
    if not paths:
        raise LibraryError(f"{tag} records no backed-up paths; restore by hand with git checkout {tag} -- <path>")
    dirty = [
        p for p in _git(cfg, "diff", "--name-only", "-z", "HEAD").stdout.split("\0")
        if p and not _under(p, paths)
    ]
    if dirty and not force:
        raise LibraryError(
            "uncommitted changes outside the backed-up paths (first: " + dirty[0]
            + "); commit or discard them, or pass --force. Nothing was changed."
        )
    present = [p for p in paths if _path_in_ref(cfg, f"refs/tags/{tag}", p) or _git(
        cfg, "ls-tree", "-d", f"refs/tags/{tag}", p, check=False).stdout.strip()]
    # Same order as every restore: edits made to these paths since the restore are
    # backed up, tagged and pushed before the checkout overwrites them.
    backup_and_tag(cfg, paths, f"backup before undoing {tag}", result, now=now)
    if present:
        _git(cfg, "checkout", f"refs/tags/{tag}", "--", *present)
    result.say("restored from " + tag + ": " + ", ".join(paths))
    result.say("edits since the restore are in the new tag above")
    result.say("files added after the backup are left in place")
    return result


# ---------------------------------------------------------------------- template merge


def _blob(cfg: Config, ref: str, path: str) -> str | None:
    proc = _git(cfg, "rev-parse", "--verify", "--quiet", f"{ref}:{path}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _index_blob(cfg: Config, path: str) -> str | None:
    proc = _git(cfg, "rev-parse", "--verify", "--quiet", f":0:{path}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _changed_paths(cfg: Config, base: str, ref: str) -> set[str]:
    out = _git(cfg, "diff", "--name-only", "--no-renames", "-z", base, ref).stdout
    return {p for p in out.split("\0") if p}


def load_kept(repo: Path) -> dict:
    path = repo / KEPT_FILE
    if not path.exists():
        return {"version": KEPT_VERSION, "entries": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise LibraryError(f"{KEPT_FILE} is not in the expected shape")
    return data


def update_from_template(cfg: Config) -> Result:
    """Merge the template into the personal repo; the user wins every conflict.

    The template's version of each overridden file is recorded as a git blob
    reference in ``.library/kept-template-versions.json``. No template copies
    are written into the tree: any .fxp under a scan root would show up in the
    browser as a duplicate patch.
    """
    result = Result("update")
    fetch_upstream(cfg, result)
    if _git(cfg, "diff", "--quiet", "HEAD", check=False).returncode != 0:
        raise LibraryError("tracked files have uncommitted changes; commit them before updating")
    if _git(cfg, "merge-base", "HEAD", cfg.upstream_ref, check=False).returncode != 0:
        raise LibraryError("HEAD and the template share no history; a merge cannot find a base")
    base = _out(cfg, "merge-base", "HEAD", cfg.upstream_ref)
    upstream_commit = _out(cfg, "rev-parse", cfg.upstream_ref)

    ours_changed = _changed_paths(cfg, base, "HEAD")
    theirs_changed = _changed_paths(cfg, base, cfg.upstream_ref)
    overlap = sorted(
        p
        for p in ours_changed & theirs_changed
        if not p.startswith(".library/") and _blob(cfg, "HEAD", p) != _blob(cfg, cfg.upstream_ref, p)
    )

    if _git(cfg, "merge-base", "--is-ancestor", cfg.upstream_ref, "HEAD", check=False).returncode == 0:
        result.say("already up to date with the template")
        return result

    proc = _git(cfg, "merge", "--no-ff", "--no-commit", "-X", "ours", cfg.upstream_ref, check=False)
    merging = (cfg.repo / ".git").exists() and _git(cfg, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0
    if proc.returncode not in (0, 1) or (proc.returncode == 1 and not merging):
        if merging:
            _git(cfg, "merge", "--abort", check=False)
        raise LibraryError(f"merge failed before it could start: {(proc.stderr or proc.stdout).strip()}")

    # Conflicts that -X ours cannot settle (modify/delete, add/add): user wins.
    unmerged = sorted({p for p in _git(cfg, "diff", "--name-only", "--diff-filter=U", "-z").stdout.split("\0") if p})
    for path in unmerged:
        stages = {line.split()[2] for line in _out(cfg, "ls-files", "-u", "--", path).splitlines() if line}
        if "2" in stages:
            _git(cfg, "checkout", "--ours", "--", path)
            _git(cfg, "add", "--", path)
        else:
            _git(cfg, "rm", "-q", "--", path)

    kept = load_kept(cfg.repo)
    seen = {(e.get("path"), e.get("blob")) for e in kept["entries"]}
    recorded = 0
    for path in overlap:
        mine = _blob(cfg, "HEAD", path)
        merged = _index_blob(cfg, path)
        if merged != mine:
            continue  # the template's change (or a clean text merge) went in; nothing was overridden
        theirs = _blob(cfg, cfg.upstream_ref, path)
        if (path, theirs) in seen:
            continue
        kept["entries"].append({"path": path, "upstream_commit": upstream_commit, "blob": theirs})
        seen.add((path, theirs))
        recorded += 1
    kept["entries"].sort(key=lambda e: (e["path"], e.get("upstream_commit", ""), e.get("blob") or ""))
    kept_path = cfg.repo / KEPT_FILE
    kept_path.parent.mkdir(parents=True, exist_ok=True)
    kept_path.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _git(cfg, "add", "--", KEPT_FILE)

    _git(cfg, "commit", "-q", "-m", f"Merge template {upstream_commit[:10]} (user wins on {len(overlap)} overlapping path(s))")
    result.backup_commit = _out(cfg, "rev-parse", "HEAD")
    result.say(f"merged template {upstream_commit[:10]}; {recorded} template version(s) recorded in {KEPT_FILE}")
    return result


def absorb_commit(cfg: Config, commit: str) -> Result:
    """``merge -s ours`` a one-off commit (the template's strip commit) exactly once."""
    result = Result("absorb")
    if _git(cfg, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", check=False).returncode != 0:
        raise LibraryError(f"unknown commit {commit!r}; fetch the template first")
    if _git(cfg, "merge-base", "--is-ancestor", commit, "HEAD", check=False).returncode == 0:
        result.say(f"{commit[:10]} is already part of this history")
        return result
    if _git(cfg, "diff", "--quiet", "HEAD", check=False).returncode != 0:
        raise LibraryError("tracked files have uncommitted changes; commit them first")
    _git(cfg, "merge", "-s", "ours", "--no-edit", commit)
    result.say(f"absorbed {commit[:10]}; its changes were not applied")
    return result


# ------------------------------------------------------------- store-hit verification


def _device_home() -> Path:
    return Path(os.environ.get("MPE_HOME") or Path.home())


def default_scan_roots() -> list[Path]:
    home = _device_home()
    resources = Path(os.environ.get("MPE_SURGE_RESOURCES") or home / "surge" / "resources" / "data")
    docs = Path(os.environ.get("MPE_SURGE_DOCS") or home / "Documents" / "Surge XT")
    return [resources / "patches_factory", resources / "patches_3rdparty", docs / "Patches"]


def _store_keys(path: Path, label: str) -> set[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(raw, dict):
        return set()
    if label == "metadata":
        raw = raw.get("patches") if isinstance(raw.get("patches"), dict) else {}
    return {k for k in raw if not k.startswith("_")}


def store_hit_snapshot(roots: list[Path], store_paths: dict[str, Path]) -> dict:
    """Count patches per root and, per store, the keys that land on a real patch.

    The root's *name* decides the key prefix, exactly as the browser does, so a
    renamed link shows up here as zero hits.
    """
    from patch_browser.patch_identity import patch_root_label, stable_key_for_relative_path

    keys: set[str] = set()
    stems: set[str] = set()
    per_root: dict[str, int] = {}
    for root in roots:
        count = 0
        if root.is_dir():
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    if not f.lower().endswith(".fxp"):
                        continue
                    rel = Path(dirpath, f).relative_to(root)
                    keys.add(stable_key_for_relative_path(patch_root_label(root), rel))
                    stems.add(rel.stem)
                    count += 1
        per_root[root.name] = count
    hits: dict[str, dict] = {}
    for label, path in sorted(store_paths.items()):
        found = _store_keys(path, label)
        on_keys = {k for k in found if k in keys}
        on_stems = {k for k in found if ":" not in k and k in stems}
        hits[label] = {"keys": len(found), "stable_hits": len(on_keys), "legacy_stem_hits": len(on_stems)}
    return {"patches_per_root": per_root, "total_patches": sum(per_root.values()), "store_hits": hits}


def compare_snapshots(before: dict, after: dict) -> list[str]:
    """Differences that mean a setting or patch went missing; empty list is a pass."""
    problems: list[str] = []
    for root, n in before["patches_per_root"].items():
        m = after["patches_per_root"].get(root, 0)
        if m < n:
            problems.append(f"{root}: {n} patches before, {m} after")
    for label, b in before["store_hits"].items():
        a = after["store_hits"].get(label, {})
        for field_name in ("stable_hits", "legacy_stem_hits"):
            if a.get(field_name, 0) < b[field_name]:
                problems.append(f"{label}: {field_name} {b[field_name]} before, {a.get(field_name, 0)} after")
    return problems


def default_store_paths() -> dict[str, Path]:
    home = _device_home()
    env = {
        "fx": "MPE_FX_FILE",
        "hold": "MPE_HOLD_FILE",
        "pressure": "MPE_PRESSURE_FILE",
        "normalization": "MPE_NORMALIZATION_FILE",
    }
    out: dict[str, Path] = {}
    for label, file_name in STORE_FILES.items():
        override = os.environ.get(env.get(label, ""), "").strip() if label in env else ""
        out[label] = Path(override).expanduser() if override else home / f".{file_name}"
    return out


# --------------------------------------------------------------------------- CLI


def _config_from_args(args: argparse.Namespace) -> Config:
    repo = args.repo or os.environ.get("MPE_PERSONAL_REPO", "")
    if not repo:
        raise LibraryError("no library checkout: pass --repo or set MPE_PERSONAL_REPO")
    path = Path(repo).expanduser()
    if not (path / ".git").exists():
        raise LibraryError(f"{path} is not a git checkout")
    return Config(
        repo=path.resolve(),
        origin=os.environ.get("MPE_LIBRARY_ORIGIN_REMOTE", "origin"),
        upstream=os.environ.get("MPE_LIBRARY_UPSTREAM_REMOTE", "upstream"),
        ref=os.environ.get("MPE_LIBRARY_UPSTREAM_REF", "main"),
        fetch=not args.no_fetch,
        push=True,
        allow_offline=args.allow_offline,
        live_home=Path(args.live_home).expanduser() if getattr(args, "live_home", None) else None,
        device_id=getattr(args, "device_id", None),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mpe-library", description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", help="personal library checkout (default: $MPE_PERSONAL_REPO)")
    p.add_argument("--no-fetch", action="store_true", help="use the last fetched template ref (offline)")
    p.add_argument(
        "--allow-offline",
        action="store_true",
        help="if the backup push fails, go ahead anyway (commit and tag stay local)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    live = argparse.ArgumentParser(add_help=False)
    live.add_argument("--live-home", help="the device's home dir: its live stores are copied into settings/ before the backup")
    live.add_argument("--device-id", help="device folder under settings/device/ (with --live-home)")
    live.add_argument("--no-live-snapshot", action="store_true", help="skip the live copy (settings/ may be stale)")
    sp = sub.add_parser("restore-patch", parents=[live], help="restore one patch and clear its settings")
    sp.add_argument("stable_key", help="e.g. factory:Bass/Lead 1")
    sub.add_parser("restore-stock", help="restore stock patches and CC0 collections; yours untouched")
    sub.add_parser("factory-reset", parents=[live], help="stock + your patches + per-patch settings back to the template")
    sub.add_parser("update", help="merge the template; your edits win, template blobs recorded")
    su = sub.add_parser("undo", help="put back the paths a restore backed up (not the whole tree)")
    su.add_argument("tag")
    su.add_argument("--force", action="store_true")
    sa = sub.add_parser("absorb", help="merge -s ours a one-off template commit (the strip commit)")
    sa.add_argument("commit")
    sv = sub.add_parser("verify", help="count patches and settings hits; save or compare a snapshot")
    g = sv.add_mutually_exclusive_group()
    g.add_argument("--save", metavar="FILE")
    g.add_argument("--compare", metavar="FILE")
    return p


def _cmd_verify(args: argparse.Namespace) -> int:
    snap = store_hit_snapshot(default_scan_roots(), default_store_paths())
    if args.save:
        Path(args.save).write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"saved: {snap['total_patches']} patches -> {args.save}")
        return 0
    if args.compare:
        before = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        problems = compare_snapshots(before, snap)
        print(json.dumps(snap, indent=2, sort_keys=True))
        if problems:
            print("MISMATCH:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        print("verify: nothing went missing")
        return 0
    print(json.dumps(snap, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "verify":
            return _cmd_verify(args)
        cfg = _config_from_args(args)
        if args.cmd in ("restore-patch", "factory-reset") and cfg.live_home is None and not args.no_live_snapshot:
            raise LibraryError(
                "settings/ may be older than the device's live stores. Pass --live-home <device home> "
                "to copy them in first, or --no-live-snapshot to accept a stale settings/."
            )
        if args.cmd == "undo":
            result = undo_restore(cfg, args.tag, force=args.force)
        elif args.cmd == "restore-patch":
            result = restore_patch(cfg, args.stable_key)
        elif args.cmd == "restore-stock":
            result = restore_stock(cfg)
        elif args.cmd == "factory-reset":
            result = factory_reset(cfg)
        elif args.cmd == "update":
            result = update_from_template(cfg)
        else:
            result = absorb_commit(cfg, args.commit)
    except LibraryError as exc:
        print(f"mpe-library: {exc}", file=sys.stderr)
        return 1
    if result.tag:
        print(f"backup: {result.backup_commit[:10]}  tag: {result.tag}  pushed: {'yes' if result.pushed else 'no'}")
        print(f"undo:   mpe-library undo {result.tag}   (only the backed-up paths)")
    for note in result.notes:
        print(note)
    if result.action in ("restore-patch", "factory-reset"):
        print("next:   stop the patch browser, run scripts/library-snapshot-settings.sh --in "
              "(replaced live files are kept under ~/.mpe-library-backups), start it again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
