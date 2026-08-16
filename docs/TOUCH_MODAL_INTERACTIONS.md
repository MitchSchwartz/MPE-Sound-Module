# Touch modal interaction contract

*Last updated: 2026-08-15 (America/Toronto)*

The Pi browser is **pygame + evdev**, not a toolkit. Basic interactions (scroll, tap vs drag, momentum, off-screen affordances) must be **explicit per surface** — they do not come for free.

This doc is the **contract** for new UI and the **audit checklist** before shipping modal work.

---

## Primitives (use these — do not reinvent)

| Primitive | Use when | Momentum | Tap vs scroll |
|-----------|----------|----------|----------------|
| **`ScrollList`** | Main nav lists (folders, browse rows, All patches) | Yes | Built-in |
| **`ContentScrollArea`** | Arbitrary vertical content in a fixed viewport (settings body, WiFi network list) | Yes | `pointer_up` → scrolled? skip tap |
| **`ScrollableActionList`** | Bottom-sheet **action menus** (context menu, folder picker, instrument picker) | Yes | Same as `ContentScrollArea` |
| **`TouchPressState`** | **Pressed visual** on any tappable control (buttons, rows, keys, chips) | — | Set on `pointer_down`, clear on `pointer_up` or scroll gesture |

**Press feedback rule:** Every tappable control must show a pressed state while the finger is down. Use `self._touch_press.set("<id>")` on pointer down and `pressed=self._pressed("<id>")` (or `ScrollableActionList.pressed_action_id`) when drawing. Clear on pointer up via `_clear_modal_pointer()` / `_clear_settings_pointer()` or when a scroll gesture starts.

**Rule:** Any list that can exceed **~5 rows** on 480×320 must use one of the scroll primitives above. Never clamp panel height without wiring scroll + hit-testing.

---

## Pointer routing pattern (every scroll surface)

1. **`pointer_down`** — if inside scroll viewport, start tracking (do not fire actions).
2. **`pointer_move`** — update scroll; mark gesture as scroll if movement &gt; threshold.
3. **`pointer_up`** — if scroll gesture, **do not** hit-test rows; else `action_at(pos)` / row tap.
4. **`tick(dt)`** in main loop while that screen is active — momentum decay.
5. **Evdev path** — non-`BROWSER` screens inject pygame events; same handlers must run (no browser-only wiring).

---

## Screen audit (480×320 reference)

| Screen | Scroll primitive | Pointer wired | Momentum tick | Off-screen hint | Notes |
|--------|------------------|---------------|---------------|-----------------|-------|
| Browser nav (`ScrollList`) | ScrollList | evdev + SDL | Yes | — (gap — see below) | OK |
| All patches + A–Z rail | ScrollList | evdev + SDL | Yes | Rail feedback | OK |
| Browse carousel + filter pane | `BrowseCarousel` (own primitive — track offset, not a list scroll) | evdev + SDL | — (instant snap, see below) | — | Edge-pan Home↔Filter; filter tags persist, never auto-close |
| **Context / instrument / folder picker** | **ScrollableActionList** | SDL + evdev inject | **Yes** | **Hairline hints** | Fixed 2026-08-08 |
| Name prompt (keyboard) | — | tap keys | — | — | OK (fixed layout) |
| Settings panel | ContentScrollArea | dedicated handlers | Yes | — | OK |
| WiFi modal (list) | ContentScrollArea | dedicated handlers | Yes | — | OK |
| Surge buffer / sample-rate pickers | Static short lists | tap | — | — | OK (&lt;5 rows) |
| Brightness modal | Slider | drag | — | — | OK |
| Theme modal (colors view) | `ContentScrollArea` | tap + swipe | momentum | edge hints | Presets / saved / custom swatches scroll; fixed Back footer |
| Power / calibrate confirms | Static | tap | — | — | OK |

**Browse carousel notes:**

- **Instant snap, not tweened.** The spec's own open question permits either; tweening the offset would mean `BrowseCarousel.offset_px` is mid-animation when `_layout()` reads it for hit-testing, which conflicts with Phase A/C's tested contract that `end_drag()` leaves `offset_px` at the exact target immediately. Deferred, not forgotten — if this needs a tween later, animate a separate `_browse_visual_offset` used only by draw, not the value `_layout()` positions rects from.
- **Nav list has no scroll-edge hint** (`draw_vertical_scroll_edge_hints` needs a `ContentScrollArea`-shaped `edge_hint_strength()`; `ScrollList` — what backs `nav_list` — doesn't track that state). This was already true before the carousel (nav list is not new), but the carousel's full-height nav list goal makes it more likely to actually overflow. Adding hint tracking to `ScrollList` is a shared-widget change affecting every `ScrollList` caller, not a browse-carousel-scoped one — left as a follow-up, not implemented here.
- **Zone priority is classify-once:** pointer-down picks `edge_carousel` / `filter_tap` / "not ours" via `patch_browser/gesture_router.py`; move/up consume the stored result rather than re-hit-testing. Mixer/nav/tap dispatch is untouched — the filter pane and mixer/patch pane never spatially overlap (opposite ends of the track), so this doesn't risk misrouting a mixer drag.

---

## Pre-ship checklist (copy into PR / task)

For any new or changed touch surface:

- [ ] Lists &gt; 5 rows use `ScrollList`, `ContentScrollArea`, or `ScrollableActionList`
- [ ] `pointer_down` / `move` / `up` wired for that `Screen` enum value
- [ ] `tick(dt)` called in `TouchPatchBrowser.run()` when screen active
- [ ] Evdev inject path exercises the same handlers (Pi soak, not mouse-only)
- [ ] Hit-testing uses **scroll offset** (content-local coords)
- [ ] Scroll gesture suppresses accidental row activation
- [ ] **Press feedback:** tappable targets highlight while held (`TouchPressState` or `ScrollableActionList.pressed_action_id`)
- [ ] Optional: muted hairline when content extends above/below viewport

**Automated:** `tests/test_scrollable_action_list.py` + existing nav tests. **Manual:** open Set instrument → scroll to Percussion → tap (Pi).

---

## Why not a full UI library?

Options considered:

- **Kivy / BeeWare** — heavy migration; pygame + Surge loop is entrenched on Pi.
- **Dear PyGui** — immediate mode; still a large dependency shift.

**Chosen model:** thin **internal widgets** in `patch_browser/scroll_widgets.py`, one **`Screen` enum** per overlay, shared constants in `touch_ui_constants.py`. New modals copy WiFi or `ScrollableActionList` — not a third scroll implementation.

Future: a single `TouchModalHost` mixin that registers `{Screen → scroll + handlers}` to reduce copy-paste.

---

## Related

- [`docs/TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md) — feature overview
- [`patch_browser/touch_press.py`](../patch_browser/touch_press.py) — pressed-state tracker
- [`patch_browser/scroll_widgets.py`](../patch_browser/scroll_widgets.py) — scroll + action sheet primitives
- [`patch_browser/touch_ui_enums.py`](../patch_browser/touch_ui_enums.py) — `Screen` states
- [`Documents/specs/touch-browser-browse-carousel-spec.md`](../Documents/specs/touch-browser-browse-carousel-spec.md) — browse carousel + filter pane spec
