# Touch patch browser — instruments, favorites v2, nested nav

**Status:** Draft (design)  
**Last updated:** 2026-08-08 (America/Toronto)  
**Depends on:** #10 shipped (All patches + A–Z rail)  
**Issues:** [#11](https://github.com/MitchSchwartz/MPE-Module/issues/11) · [#21](https://github.com/MitchSchwartz/MPE-Module/issues/21) · [#24](https://github.com/MitchSchwartz/MPE-Module/issues/24)  
**Prior spec:** [touch-patch-browser-browse-ux-spec.md](touch-patch-browser-browse-ux-spec.md) (#10, shipped)

---

## Problem

The library is ~3k patches. Factory folder names help but do not equal **instrument type** (many opaque names). **Quick Access** (`MPE_FAVORITES_NAME`, default `!Quick Access`) is a flat copy folder — no subfolders in nav, no user organization beyond yes/no heart.

Additional constraints discovered post-#10:

- `PatchScanner` indexes only the **first path segment** as category; nested folders are invisible.
- Same **patch name** in different subfolders of one category **collides** (dedup by name → silent data loss).
- Per-patch sidecars (volume, hold, pressure, normalization) key by **stem**, not path — same-named patches share settings.
- **Back** always jumps to top-level folder list, not one level up.
- Every favorite toggle runs a **full synchronous rescan**.

---

## Goals

| Goal | Outcome |
|------|---------|
| Browse by instrument | Filter chips (Piano, Pad, Bass, …) over flat or tree lists |
| Deeper favorites | Spotify model: default **Liked** + user subfolders under Quick Access |
| Metadata | Sidecar index: path segments + inferred instruments + user overrides |
| Nested nav | Drill into subfolders (Quick Access and library); back = one level up |
| Long-press actions | Context menu on folder/patch rows (after favorites v2 for move/bulk) |
| Nav reliability | **Mandatory** transition layer (#24) before new modes stack on |

---

## Non-goals (this epic)

- Full FSM rewrite of draw/input mixins
- OSK / prefix search (unless chips + A–Z fail device testing — #21 deferred)
- Pin arbitrary factory folders to nav (Phase 6)
- Reorganizing factory/3rdparty patch files on disk
- Symlink-based favorites (see [Favorites on disk](#favorites-on-disk-copies-not-symlinks))

---

## Reference UX patterns

| Source | Pattern |
|--------|---------|
| Zynthian | Horizontal category tabs / listbox; drag-scroll when many categories |
| Elektron Model:Samples | Short back = up one level; long back = root (optional later) |
| Spotify | Default “Liked” bucket + user playlists/folders |
| Surge XT desktop | `CAT=` metadata search; factory folder names as taxonomy hints |

**Target:** SmartiPi 800×480 landscape — chip row under nav header; long-press → bottom sheet (52px+ rows, finger-up to activate).

---

## Architecture decisions

### Favorites on disk: copies, not symlinks

**Decision: keep `.fxp` file copies** under `!Quick Access/<folder>/`.

| | Copies (chosen) | Symlinks (rejected) |
|---|-----------------|---------------------|
| Offline gig | Quick Access folder is self-contained on the Pi | Breaks if source path missing or library not fully synced |
| PC curation | Copy/deploy Quick Access tree as today | Symlinks may not survive rsync/zip to device |
| Surge compatibility | Proven path today (`copy_patch_to_favorites`) | Surge project warns symlink traversal can misbehave (esp. Windows; risk on Pi is lower but nonzero) |
| Source updates | Favorite copy is stale until re-favorited | Auto-tracks source edit |
| Disk | Small `.fxp` files; gig sets are tens of patches, not 3k | Minimal duplication |

**Implementation note:** copies stay; **stop** calling full `scan_patches()` on every heart toggle. Favorites v2 maintains an **in-memory index** + `~/.patch_browser_favorites.json` mapping; rescan only the Quick Access subtree or update index in place.

### Path-based stable key

All new metadata and retrofitted sidecars use a **stable key** derived from canonical path under patch roots (not stem alone):

```text
stable_key = "<root_id>:<relative_posix_path_without_ext>"
```

Example: `factory:Bass/Sub/Deep Growl`

Retrofit **normalization, hold, pressure** to path-based keys as part of Phase 1b (migration: stem → path where unambiguous; prompt/manual merge where colliding).

### Metadata index (simple v1 schema)

Phase 1 ships minimal fields; defer multi-source provenance merge until `.fxp` spike (Phase 6).

```json
{
  "version": 1,
  "patches": {
    "factory:Bass/Sub/Deep Growl": {
      "name": "Deep Growl",
      "path": "/home/.../patches_factory/Bass/Sub/Deep Growl.fxp",
      "path_segments": ["Bass", "Sub"],
      "instruments": ["bass"],
      "instrument_user": null
    }
  }
}
```

- **PC batch** (repo-shipped baseline for factory + 3rdparty): heuristic on name + all folder segments.
- **On-device:** long-press “Set instrument…” writes `instrument_user` (overrides inference).
- **Phase 6 optional:** merge `fxp_category` from Surge patch-tool spike.

Files:

- Shipped: `data/patch_metadata_baseline.json` (repo)
- User overrides: `~/.patch_browser_metadata.json`

### Favorites v2 data model

```json
{
  "version": 1,
  "folders": ["Liked", "Gig A", "Warm Pads"],
  "entries": {
    "factory:Piano/Grand/Concert D": {
      "folder": "Liked",
      "dest_path": ".../Quick Access/Liked/Concert D.fxp",
      "added_at": "2026-08-08T12:00:00-04:00"
    }
  }
}
```

- First heart → copy to `Quick Access/Liked/` + index entry.
- User-created folders → subdirs under Quick Access only.
- Unfavorite → delete copy + remove index entry; **never** delete source patch.

### Navigation stack (mandatory #24)

Replace ad-hoc `left_nav_mode` + `browse_folder_index` mutations with:

```python
@dataclass
class NavFrame:
    kind: Literal["folders", "patches", "all_patches", "instrument_filter"]
    folder_path: tuple[str, ...]   # segments from patch root
    scroll_y: float
    scroll_velocity: float
    row_height: int
    highlight_index: int | None
    instrument_filter: str | None  # None = all
```

**API (names TBD in implementation):**

- `_push_nav_frame(**ctx)` — save scroll/momentum, apply new frame, `_relayout()` when geometry changes
- `_pop_nav_frame()` — restore parent scroll; **back button**
- `_enter_nav_mode(...)` — thin wrapper; single owner of highlight/jump cleanup (#24)

**Promotion:** #24 is **mandatory gate**, not optional spike. Deliverable = transition helper + explicit transition table in `docs/TOUCH_PATCH_BROWSER.md` + tests (acceptance list below) **before** Phase 0.5 scanner work adds new nav modes.

#### #24 acceptance tests (gate)

- [ ] Enter All patches from folder list
- [ ] Leave All patches (back) — scroll restored
- [ ] Pick patch from All — loads, detail shown, nav sane
- [ ] **Current** chip — jumps to loaded patch folder
- [ ] Back from patch list — **one level up** (after Phase 2 tree; until then, back to folders)
- [ ] No zero-width main rect after any transition
- [ ] No ghost highlight row in All patches during scroll

---

## Instrument taxonomy (v1)

Fixed vocabulary; multi-tag allowed; **one primary** for chip filter default.

`piano`, `keys`, `organ`, `bass`, `lead`, `pad`, `brass`, `strings`, `woodwind`, `pluck`, `synth`, `fx`, `other`

Chip row: `All` + primaries with ≥1 patch (hide empty).

---

## UI flows

### Chip filter

- Row under nav header (scroll horizontally if needed).
- Filters current list context (folder tree, All patches, Quick Access subtree).
- Row subtitle: `pad · Bass/Sub` when name is opaque.

### Long-press menu (~600ms)

Cancel on scroll/drag beyond threshold.

| Target | Phase 5 actions |
|--------|-----------------|
| Folder | Add all to Quick Access (Liked); Add all to… → subfolder |
| Patch | Favorite / Unfavorite; Move to folder…; Set instrument… |
| Quick Access folder | New subfolder; Rename; Delete if empty |

Phase 3 slim menu (if any early long-press): favorite/unfavorite flat only — full menu waits for Phase 5.

### Back button

- **Tap ◀:** pop nav stack one frame.
- **Long ◀ (optional, Phase 6+):** pop to root (Elektron pattern).

---

## Phasing

```text
Phase 0   Nav transition layer (#24) — MANDATORY GATE
Phase 0.5 Tree scan + path-based patch identity (collision fix)
Phase 1   Metadata baseline (PC batch + on-device merge)
Phase 1b  Retrofit stable_key into norm / hold / pressure sidecars
Phase 2   Nested folder navigation (#11)
Phase 3   Favorites v2 (Liked + user folders + migration)
Phase 4   Instrument chips
Phase 5   Long-press context menus (full)
Phase 6   .fxp metadata spike, pin folders, OSK (if needed)
```

### Phase 0 — Nav transition layer (#24)

**Issues:** #24 (promoted mandatory)

- Implement `_enter_nav_mode` / nav stack foundation
- Transition table documented
- Acceptance tests above — all green on `dev`

**Do not start Phase 0.5 until Phase 0 tests pass.**

### Phase 0.5 — Scanner tree + stable key

- `PatchScanner` exposes folder tree; patches keyed by path
- Fix name-collision dedup bug
- Add `test_patch_scanner.py` (collisions, favorites matching, nested paths)

### Phase 1 — Metadata baseline

**Issues:** #21 (partial)

- Build `data/patch_metadata_baseline.json` on PC (script in `scripts/`)
- Heuristic classifier: name tokens + `path_segments`
- Load baseline + user overrides at scan; attach `instruments[]` to patch dicts

### Phase 1b — Sidecar stable_key migration

- Path-based keys for normalization, hold, pressure
- One-time migration with collision report (no silent merge)

### Phase 2 — Nested folder navigation

**Issues:** #11 (core)

- Nav stack drives drill-down any folder with patches
- Quick Access subfolders visible
- Back pops one level

### Phase 3 — Favorites v2

**Issues:** #11

- `~/.patch_browser_favorites.json` + in-memory index
- Default **Liked** folder; create/rename/delete user folders
- **Migration:** dry-run mode; fixture tests; verify 100% of pre-migration flat copies represented before moving files
- Copy-based disk layout under `Quick Access/<folder>/`

### Phase 4 — Instrument chips

**Issues:** #21

- Horizontal chip row + filter
- Subtitle shows primary instrument tag

### Phase 5 — Long-press menus

- Full folder/patch/QA-folder actions (see table above)

### Phase 6 — Deferred (#21 remainder)

- Surge `.fxp` XML category spike (patch-tool)
- Pin arbitrary folders to left nav
- Mini OSK if SmartiPi testing proves insufficient

---

## Testing strategy

| Area | Requirement |
|------|-------------|
| Scanner | New `tests/test_patch_scanner.py` before Phase 1 |
| Nav transitions | Phase 0 gate list; assert mode, layout rects, scroll |
| Metadata | Classifier unit tests on fixture name/path set |
| Favorites migration | Dry-run + fixture; no destructive test on real user tree in CI |
| Smoke | Extend `test_touch_browser_smoke.py` for new modes after each phase |

---

## Migration: flat Quick Access → Liked

1. **Dry-run** (`scripts/migrate-favorites-v2.py --dry-run`): list copies in flat Quick Access root; propose `Liked/` targets + JSON entries.
2. **Verify:** count(match) == count(source); abort if any copy lacks stable_key resolution.
3. **Apply:** move files into `Quick Access/Liked/`; write `favorites.json`; no deletes of non-QA files.
4. **Rollback:** backup QA tree + JSON before apply; document restore in `docs/TOUCH_PATCH_BROWSER.md`.

---

## Related docs

- [TOUCH_PATCH_BROWSER.md](../../docs/TOUCH_PATCH_BROWSER.md) — user-facing behavior (update per phase)
- [touch-patch-browser-browse-ux-spec.md](touch-patch-browser-browse-ux-spec.md) — #10 (shipped)

---

## Open questions (resolved)

| Question | Decision |
|----------|----------|
| #24 mandatory? | **Yes** — gate before Phase 0.5 |
| Favorites on disk | **Copies**, not symlinks |
| Long-press before favorites v2? | **No** — full menu in Phase 5; optional slim fav toggle earlier only if needed |
