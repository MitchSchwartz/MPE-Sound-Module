# Touch patch browser — browse carousel + filter pane

**Status:** Draft  
**Last updated:** 2026-08-15 (America/Toronto)  
**Supersedes (partial):** inline instrument chip panel behavior in [touch-browser-instruments-favorites-spec.md](touch-browser-instruments-favorites-spec.md) §Chip filter  
**Depends on:** Instrument metadata + `INSTRUMENT_VOCAB` (`patch_metadata.py`, `instrument_filter.py`) — chips can ship before full metadata epic if heuristic tags exist for current folder context  
**UX reference:** Cursor canvas `mpe-browse-filter-ux.canvas.tsx` (concept **J · Swipe carousel**, masonry filter tags)

---

## Problem

On 800×480 landscape the left nav is height-constrained. The **inline instrument chip panel** (`instrument_filter_expanded`) eats list rows and **collapses on every chip tap** (`instrument_filter_expanded = false`), which fights “retain filter while browsing” (Surge-style search persistence).

Patch detail (Vol / Tail / Touch faders) must stay full width when visible. Horizontal pan on the fader rect would conflict with mixer drag.

Gesture handling is **fragmented** today (settings swipe, `ScrollList`, mixer, future carousel) — each surface retuned independently.

---

## Goals

| Goal | Outcome |
|------|---------|
| Full-height patch list | Nav column always 268px tall — no inline chip row |
| Dedicated filter surface | Filter pane same width as patch manager (**532px**) |
| Dense instrument UI | Masonry-style tags — width follows label length; show full `INSTRUMENT_VOCAB` where possible |
| Persistent filter | Selecting a tag **does not** close filter pane or clear filter |
| Safe faders | Patch pane never participates in horizontal browse pan |
| One gesture model | Zone routing at **pointer-down**; shared horizontal pan helper |
| Minimal chrome | **No** bottom tab bar; **no** new nav-header tap targets for filter |
| List affordance | Reuse **`draw_vertical_scroll_edge_hints`** when nav list scrolls |

---

## Non-goals (v1)

| Excluded | Notes |
|----------|--------|
| **J2** (nav fixed, patch ↔ filter swap on right pane) | Documented in canvas for comparison; **not** v1 layout |
| Inner seam / column-handle swipe | Rejected — unintuitive without handle chrome |
| Nav header as swipe track | Header reserved for existing widgets; reducing touch targets |
| Filter funnel / chip toggle in nav header | **Remove** — filter reached via edge swipe only |
| Direction-lock retuning (angle-based scroll vs pan) | v1 uses **zone-at-down** only |
| Right screen edge gestures | Reserved for future global menu (12–16px); not v1 |
| Folder picker in filter pane | Folder browse stays in nav (**Up** / drill-down) |
| Third-party gesture library | No fit for pygame + evdev appliance (see DECISIONS 2026-08-15) |
| OSK / search field in filter pane | Deferred |

---

## Layout — browse track (concept J)

Three full-height columns on one horizontal track (1332px total, 800px viewport):

```text
[ Filter 532 ] [ Nav 268 ] [ Patch 532 ]
```

| Stop | Track offset (px) | Visible | Off-screen |
|------|-------------------|---------|------------|
| **Home** (default) | `-532` | Nav + Patch | Filter (left) |
| **Filter** | `0` | Filter + Nav | Patch (right) |

- **Nav** width fixed at `LEFT_NAV_WIDTH` (268).
- **Filter** and **Patch** panes each **532px** — never compressed.
- Animated track follows finger during edge pan; snap on release.
- Filter selection **live-updates** nav list in both stops.

### Filter pane UI

- **Masonry tags:** one tappable chip per instrument in `INSTRUMENT_VOCAB` (+ **All**), chip horizontal padding scales with label length, `flex-wrap` pack.
- Show **count** per tag for current list context (patches in active folder / All / QA subtree).
- Hide tags with zero patches in current context (same rule as today’s dynamic chip list).
- **No folder strip** in filter pane.
- Minimal header line only (e.g. match count) — no large title chrome.

### Deprecate inline chips

Remove from v1 browse UI:

- `instrument_filter_expanded` toggle and inline chip panel under nav header
- Nav header **filter funnel** button (`draw_filter_icon` hit target)
- Layout height eaten by `_instrument_chip_offset()`

Retain:

- `instrument_filter` state and `filter_patches_by_instrument` logic
- Long-press “Set instrument…” (Phase 5 of instruments epic) — unchanged

---

## Gesture architecture

### Principles

1. **Zone at pointer-down** decides gesture type for the **entire** contact — no mid-gesture reclassification.
2. **No angle-based direction lock** in v1 (avoids scroll “fighting” edge pan).
3. **Tune once** — shared constants module, not per-screen magic numbers.

### New modules

| Module | Responsibility |
|--------|----------------|
| `patch_browser/gesture_router.py` | `classify_pointer_down(x, y, layout) -> GestureKind` |
| `patch_browser/browse_carousel.py` | `BrowseCarousel` — offset px, follow finger, snap stops, optional release momentum |
| `patch_browser/touch_browser_browse.py` | Mixin — wire router + carousel into evdev/SDL browser path |

Existing primitives **unchanged**:

- `ScrollList` / `ContentScrollArea` — vertical scroll
- Settings panel horizontal dismiss — may later call same `BrowseCarousel` or share snap constants

### Gesture kinds (browser screen)

| Kind | Pointer-down zone | Behavior |
|------|-------------------|----------|
| `edge_carousel` | Left screen edge strip | Horizontal pan of browse track |
| `nav_scroll` | Nav list rect **excluding** edge strip | Existing `ScrollList` |
| `mixer` | Patch pane fader / norm / favorites rects | Existing mixer handlers |
| `filter_tap` | Filter pane (Filter stop only) | Tag hit-test; no horizontal pan from pane body in v1 |
| `tap` | Elsewhere | Existing browser tap routing |

**Priority (first match wins):** `edge_carousel` → `mixer` → `filter_tap` (if Filter stop) → `nav_scroll` (if in nav) → `tap`.

### Zone geometry (800×480)

| Zone | Rect | Constant |
|------|------|----------|
| Left edge (carousel) | `x ∈ [0, 48)`, full content height below status bar | `BROWSE_EDGE_GRAB_W = 48` |
| Nav list scroll | Nav column minus `[0, 48)` when Nav visible | derived |
| Patch / mixer | Patch pane rect when Home stop | derived |
| Filter tags | Filter pane rect when Filter stop | derived |

Future outer rim (not v1): `x ∈ [0, 16)` global menu — **document only**; carousel uses 48px inner band starting at `x=0` until menu ships, then carousel becomes `x ∈ [16, 64)` (DECISIONS).

### Browse carousel interaction

| Parameter | Value | Notes |
|-----------|-------|-------|
| Stops | `-532`, `0` | Home, Filter |
| Snap commit | Nearest stop after release | |
| Snap threshold | 50% of travel between stops **or** \|dx\| ≥ 56px from down | Align with settings dismiss (`touch_browser_input.py`) |
| Follow finger | Track offset += finger dx while `edge_carousel` active | Clamp to `[-532, 0]` |
| Momentum | Optional v1.1 — snap-only acceptable for v1 | |
| Filter stop + tag tap | Set `instrument_filter`; **do not** change stop | Persistence requirement |

**Edge swipe direction (user mental model):**

- **Home → Filter:** finger down in left edge, drag **right** (track offset increases toward 0).
- **Filter → Home:** finger down in left edge, drag **left** (track offset decreases toward -532).

### Affordances (draw only)

- **No bottom tab bar.**
- **No footer text** — optional subtle chevron at nav bottom / filter bottom (icons only) — **draw** hints, not required tap targets.
- **Scroll edge hints** on nav list via existing `draw_vertical_scroll_edge_hints` when list scrollable.

---

## Constants (`touch_ui_constants.py`)

Add (names stable for tests):

```python
BROWSE_EDGE_GRAB_W = 48
BROWSE_FILTER_W = 532      # 800 - LEFT_NAV_WIDTH
BROWSE_PATCH_W = 532
BROWSE_TRACK_W = 1332       # FILTER + NAV + PATCH
BROWSE_OFFSET_HOME = -532
BROWSE_OFFSET_FILTER = 0
BROWSE_SNAP_COMMIT_PX = 56  # match settings panel dismiss feel
```

---

## State

```python
@dataclass
class BrowseCarouselState:
    offset_px: float = -532.0          # default Home
    stop: Literal["home", "filter"] = "home"
    dragging: bool = False
    drag_start_x: float | None = None
    drag_start_offset: float = -532.0
```

Persist in session only (no disk) for v1 — restoring Filter stop on relaunch is **non-goal**.

`instrument_filter: str | None` — unchanged; independent of carousel stop.

---

## Phasing

```text
Phase A   gesture_router + browse_carousel (unit tests, no UI)
Phase B   Layout + draw (track offset, filter pane masonry, remove inline chips)
Phase C   Input wiring (evdev + SDL, zone routing)
Phase D   Polish (snap animation, scroll edge hints audit, docs)
```

### Phase A — Gesture core (gate)

- [ ] `gesture_router.py` with rect priority tests
- [ ] `browse_carousel.py` with snap + clamp tests
- [ ] No changes to production browser behavior yet

### Phase B — Layout + draw

- [ ] `_browse_track_offset_px` drives nav/filter/patch x positions
- [ ] Filter pane masonry draw + tag hit rects
- [ ] Remove inline chip panel + funnel btn from layout/draw
- [ ] Nav list uses full nav height

### Phase C — Input

- [ ] Pointer-down classification routes to carousel | scroll | mixer | filter_tap
- [ ] Edge pan wired through evdev browser path (same as SDL)
- [ ] Tag tap in filter pane sets filter without changing stop
- [ ] All-patches mode: carousel **enabled** (filter + nav still valid); A–Z rail unchanged

### Phase D — Polish + docs

- [ ] Snap easing (match `SETTINGS_PANEL_ANIM_SPEED` feel or shared tween)
- [ ] Update `docs/TOUCH_PATCH_BROWSER.md` browse section
- [ ] Update `docs/TOUCH_MODAL_INTERACTIONS.md` browser row
- [ ] Pi smoke on SmartiPi

---

## Acceptance criteria

| # | Criterion | Test type |
|---|-----------|-----------|
| 1 | Default stop is Home: Nav 268 + Patch 532 visible; filter off-screen left | Unit (layout) |
| 2 | Edge pan from Home reaches Filter stop when release past 50% or ≥56px | Unit (carousel) |
| 3 | Edge pan from Filter returns to Home under same thresholds | Unit (carousel) |
| 4 | Pointer-down at x=30 always classifies `edge_carousel`, never `nav_scroll` | Unit (router) |
| 5 | Pointer-down at nav x=120 classifies `nav_scroll`, not carousel | Unit (router) |
| 6 | Pointer-down on fader column classifies `mixer`, not carousel | Unit (router) |
| 7 | Tag tap in filter pane updates list; stop stays Filter | Integration |
| 8 | Tag tap does **not** set `instrument_filter_expanded` (field removed or inert) | Unit |
| 9 | Inline chip row not drawn; nav list gains ≥ chip panel height | Manual (Pi) |
| 10 | Horizontal drag on Vol fader does not move browse track | Manual (Pi) |
| 11 | Vertical nav scroll in x∈[48,268) does not move browse track | Manual (Pi) |
| 12 | Masonry shows All + instruments with count; zero-count hidden | Unit (draw layout) |
| 13 | `draw_vertical_scroll_edge_hints` still shown when nav list overflows | Manual (Pi) |

---

## Testing strategy

| Area | File / method |
|------|----------------|
| Zone router | `tests/test_gesture_router.py` — synthetic 800×480 layout rects |
| Carousel snap | `tests/test_browse_carousel.py` — offset math, snap thresholds |
| Instrument filter unchanged | Extend existing instrument filter tests |
| Smoke | `tests/test_touch_browser_smoke.py` — import + layout doesn't crash |
| Manual | `docs/TOUCH_PATCH_BROWSER.md` §Browse carousel — Pi checklist |

---

## Files (expected touch)

| File | Change |
|------|--------|
| `patch_browser/gesture_router.py` | **New** |
| `patch_browser/browse_carousel.py` | **New** |
| `patch_browser/touch_browser_browse.py` | **New** mixin |
| `patch_browser/touch_ui_constants.py` | Browse constants |
| `patch_browser/touch_browser_layout.py` | Track rects from offset |
| `patch_browser/touch_browser_draw.py` | Filter pane masonry; remove chip panel |
| `patch_browser/touch_browser_instruments.py` | Filter pane tags; remove expand/funnel |
| `patch_browser/touch_browser_input.py` | Router integration |
| `patch_browser/touch_browser_evdev.py` | Edge carousel in browser path |
| `docs/TOUCH_PATCH_BROWSER.md` | User-facing browse/filter section |
| `docs/TOUCH_MODAL_INTERACTIONS.md` | Browser gesture row |
| `Documents/DECISIONS.md` | Dated decision row |

---

## Open questions

| Question | Proposed default |
|----------|------------------|
| Animate snap vs instant snap v1? | **Tween** over ~150–200ms if cheap; else instant snap acceptable |
| Filter stop when `left_nav_collapsed`? | **Disable carousel** when nav collapsed (same as today’s filter hidden when collapsed) |
| All-patches + filter | Filter pane tags apply to flat list; A–Z rail indexes filtered list (rebuild index on filter change — existing pattern) |

---

## Related

- [touch-browser-instruments-favorites-spec.md](touch-browser-instruments-favorites-spec.md) — metadata + favorites epic; Phase 4 chip row **superseded** by this spec for browse UI
- [TOUCH_MODAL_INTERACTIONS.md](../../docs/TOUCH_MODAL_INTERACTIONS.md) — scroll primitives contract
- [TOUCH_PATCH_BROWSER.md](../../docs/TOUCH_PATCH_BROWSER.md) — ship doc (update Phase D)
