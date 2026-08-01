# Touch patch browser — All patches v1

**Status:** Draft (design)  
**Branch:** `feature/issue-10-11-browse-ux`  
**Worktree:** `../MPE-Module-wt-browse-ux`  
**Issues:** [#10 active](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/10) · [#11 parked](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/11) · [#21 backlog](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/21)

**Mockups:** [touch-patch-browser-browse-ux.canvas.tsx](/home/mitch/.cursor/projects/home-mitch-Documents-GitHub-MPE-Module/canvases/touch-patch-browser-browse-ux.canvas.tsx)

---

## v1 scope (#10)

Add **All patches** view alongside existing folder browse:

| Piece | Behavior |
|-------|----------|
| **Flat list** | All scanned patches, sorted A→Z by name |
| **Folder subtitle** | Top-level category on each row (not nav) |
| **A–Z rail** | Vertical rail right edge; tap letter → scroll jump |
| **Hearts** | ♥ if in Quick Access (`is_patch_in_favorites`); ♡ otherwise — **indicator only** in list |
| **Entry** | **All** affordance from folder nav (exact placement TBD) |
| **Load** | Tap row → Surge OSC load → now playing (Vol + Norm; heart toggle on detail) |

**Unchanged:** default boot = folder browse + last patch; no folder CRUD on device.

---

## Data

Build once after scan:

```text
all_patches = sorted(flatten(scanner.patches), key=name.casefold())
letter_index = { A: first_row_index, B: …, #: non-alpha }
```

No new metadata files. Fields: `name`, `path`, `category`, favorites check via existing scanner helpers.

---

## UI states

| State | Notes |
|-------|--------|
| Folder browse | Current two-pane; add **All** entry |
| `ALL_PATCHES` | Full-width list + A–Z rail; hide fader |
| Now playing | After load; existing Vol + Norm + heart |

---

## Parked (#11, #21)

Quick Access subfolders, folder chips, prefix search, OSK, `.fxp` parse, pin arbitrary folders, list-row heart toggle.

---

## Implementation order

1. `LeftNavMode.ALL_PATCHES` (or equivalent) + flatten helper
2. Letter index + `scroll_to_index` on rail tap
3. Row draw: heart + name + folder subtitle
4. **All** button + back to folder nav
5. Hide fader in All patches mode
6. Tests + `docs/TOUCH_PATCH_BROWSER.md`
