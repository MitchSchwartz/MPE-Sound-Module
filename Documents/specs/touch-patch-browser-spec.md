# Touch Patch Browser (SmartiPi / 5" landscape)

**Issue:** untracked
**Status:** Draft
**Created:** 2026-07-24

## Problem Statement

The encoder + 1.3" OLED patch browser works but scrolling and button holds are unreliable. A second Pi with a **SmartiPi ~5" landscape** touch screen is available to validate a **touch-only** UI: every feature exposed on-screen, minimal visual noise while playing, and **software brightness** control for stage/dim-room use.

## Acceptance Criteria

| # | Criterion | Test Type |
|---|-----------|-----------|
| 1 | Touch UI lists categories and patches; tap loads patch via OSC (same paths as OLED browser) | Manual |
| 2 | Idle/play screen shows currently loaded patch; one tap opens browser without loading spurious patches | Manual |
| 3 | Brightness slider adjusts `/sys/class/backlight/*/brightness` and persists across reboot | Manual |
| 4 | Power menu offers shutdown / restart / cancel with confirmation | Manual |
| 5 | Last patch restores on boot (reuse `~/.patch_browser_last_patch.json`) | Manual |
| 6 | Surge health failure surfaces restart option | Manual |
| 7 | Coexists with encoder build — selectable via service or `MPE_UI_MODE` | Manual |

## Non-Goals

- Surge parameter editing on device (PC workflow unchanged)
- On-device copy-to-favorites (disabled on encoder build; PC workflow only)
- Replacing boot/shutdown OLED animations on the reference encoder hardware
- Full desktop environment requirement (target: KMS/framebuffer + pygame)

## Security Considerations

- **Data flow:** Local patch paths and OSC to `127.0.0.1:53280` only; brightness via sysfs.
- **Trust boundaries:** N/A — single-user appliance, no network UI.
- **Auth model:** N/A — physical access only; power actions use existing `sudo poweroff/reboot` pattern.
- **Failure modes:** Brightness write fails gracefully if permissions missing; OSC failures show error toast.
- **RLS:** N/A

## Assumptions & Constraints

- Pi OS with SmartiPi / 5" landscape touch panel; primary target **800×480** (DSI or HDMI+USB). UI scales if the panel reports other sizes.
- Surge XT CLI + patch symlinks already configured per existing docs.
- Backlight sysfs may require udev rule or group membership (documented in setup guide).
- Reuses `PatchScanner`, `PatchLoader`, `SurgeMonitor` from `patch_browser_ui.py`.

## Technical Notes

- **UI stack:** pygame fullscreen, dark theme, 48px+ touch targets.
- **Interaction model:**
  - **Home:** compact now-playing strip + two-pane browser (categories list + patch title rows).
  - **Patch rows:** ~58px title rows (recommended) — balances visibility (~7 on screen) with medium-precision touch. Cards deferred unless Quick Access-only workflow wins in testing.
  - **Settings sheet:** brightness slider, power, Surge status.
- **Brightness:** `patch_browser/backlight.py` → sysfs 0–max, persisted to `~/.patch_browser_brightness.json`.
- **Service:** `touch-patch-browser.service` with `SDL_VIDEODRIVER=kmsdrm` when no X11.

## Open Questions Resolved

| Question | Decision |
|----------|----------|
| Separate app vs refactor OLED UI? | Separate `touch_patch_browser.py` importing shared logic; extract more later if both stabilize. |
| Load debounce on touch? | Tap-to-load is immediate; no scroll-stop debounce. |
| Separate playing screen? | **No** — browser is home; now-playing strip only. |
