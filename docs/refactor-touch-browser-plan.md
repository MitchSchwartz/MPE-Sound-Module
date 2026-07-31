# TouchPatchBrowser god-object refactor plan

*Split from monolithic `touch_patch_browser.py` (2656 lines) into `patch_browser/` modules.*

## Boundaries

| Module | Responsibility |
|--------|----------------|
| `touch_ui_constants.py` | Layout, scroll, mixer, settings dimensions |
| `touch_ui_enums.py` | `Screen`, `CalibrateMode`, `LeftNavMode`, audio profile label |
| `scroll_widgets.py` | `ScrollList`, `ContentScrollArea` (inertial touch scroll) |
| `mixer.py` | `MixerChannel` dataclass |
| `ui_prefs.py` | Volume + UI JSON persistence (shared helpers) |
| `touch_browser_app.py` | `TouchPatchBrowser` orchestrator: `__init__`, `run`, fonts, toast |
| `touch_browser_evdev.py` | evdev touch bridge + browser touch routing |
| `touch_browser_prefs.py` | Volume, brightness, theme, CPU meter prefs |
| `touch_browser_layout.py` | Main/settings layout geometry |
| `touch_browser_patches.py` | Scan, folder nav, patch load, favorites |
| `touch_browser_mixer.py` | Vertical fader input + value mapping |
| `touch_browser_normalization.py` | Per-patch/global norm toggles, calibration handoff |
| `touch_browser_draw.py` | All pygame draw helpers and screen composition |
| `touch_browser_input.py` | Event loop, settings/power/cal modals, `_draw` dispatch |
| `touch_patch_browser.py` | **Entry point** — re-exports `TouchPatchBrowser`, `main` |

## Design choices

- **Mixin composition** keeps method bodies identical to pre-refactor behavior; no protocol/lazy-import indirection.
- **Public script path unchanged** — systemd and `start-touch-patch-browser.sh` still invoke `touch_patch_browser.py`.
- **`patch_browser_ui.py` (OLED)** — logging cleanup landed in the grumpy audit pass (~241-line diff); not part of the touch-browser mixin split itself.

## Deferred

- Trim duplicate imports in generated mixin headers (cosmetic).
- Extract static draw helpers (`_draw_chevron`, `_draw_sidebar_panel_icon`) to a shared `draw_primitives.py` if OLED UI needs them.
- Typed host protocol for mixin `self` if mypy is added later.
