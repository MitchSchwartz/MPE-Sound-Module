# Touch Patch Browser (SmartiPi / 5" landscape)

Fullscreen touch UI for the second Pi + **SmartiPi touch screen** (~5", **landscape**). Same Surge patch library and OSC loading as the encoder/OLED build — touch replaces every hardware control.

**Target resolution:** 800×480 landscape (typical for 5" DSI and HDMI panels). The UI auto-sizes if your panel reports something else — confirm on the Pi with:

```bash
fbset -s 2>/dev/null || cat /sys/class/graphics/fb0/virtual_size
```

## Local setup (SmartiPi Pi)

Run **on the Pi** after flashing Trixie Lite, cloning the repo, and building Surge ([`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md) step 3 — skip the OLED wiring step):

```bash
cd ~/MPE-Module
git pull origin main
cp config/mpe.env.example config/mpe.env
echo 'MPE_UI_MODE=touch' >> config/mpe.env
./scripts/setup-touch-pi.sh
sudo reboot
```

After reboot, the touch browser should start fullscreen. Verify:

```bash
systemctl is-enabled touch-patch-browser patch-browser
systemctl status surge-xt-cli touch-patch-browser
journalctl -u touch-patch-browser -n 30
```

**One-time sudoers** (power menu + start script stopping other services): add to `sudo visudo`:

```
your-user ALL=(ALL) NOPASSWD: /sbin/poweroff, /sbin/reboot, /bin/systemctl, /home/your-user/MPE-Module/scripts/set-audio-profile.sh, /home/your-user/MPE-Module/scripts/set-surge-audio.sh
```

(`set-audio-profile.sh` is used by the **USB Audio** settings toggle; `set-surge-audio.sh` by **Audio buffer** / **Sample rate** debug rows.)

See [`docs/POWER_BUTTON_SETUP.md`](POWER_BUTTON_SETUP.md) for the encoder Pi pattern.

## Design goals

- **Browser is home:** patch list + detail on one screen; no separate playing mode
- **Non-disruptive:** dark theme, minimal chrome
- **Every feature on-screen:** browse, settings, brightness, power (no encoder)
- **Borrowed patterns:** `PatchScanner`, `PatchLoader`, `SurgeMonitor`, favorites folder, last-patch restore from `patch_browser_ui.py`

Interaction model:

| **Left nav (expanded)** | **Patches** in current folder, or **Folders** after Up | Tap patch → load + detail on main |
| **Up** | Switch left nav to folder list | Does not load anything |
| **All** | Flat A–Z list of every patch (hearts show Quick Access) | Hides patch detail until a row is tapped |
| **Current** | When browsing another folder — jump to loaded patch's folder + patch list | |
| **< collapse** | Nav hides; `>` tab remains to expand | Main detail gets full width |
| **Main (right)** | Selected patch: **Vol**, **Tail**, and **Touch** faders; **Norm** when Norm. on; **Norm.** toggle | No back button — list is always on the left |

## Hardware

- Raspberry Pi 5 (or 4) in a SmartiPi (or similar) case with ~5" **landscape** touch panel
- Most panels: **800×480** via DSI or HDMI+USB touch
- Same USB audio + MPE MIDI stack as the reference build
- **No encoder required** on this test rig

## Software prerequisites

On the touch Pi:

```bash
sudo apt update
sudo apt install -y python3-pygame libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0
cd ~/MPE-Module
pip3 install -r requirements.txt
```

Ensure Surge CLI and patch symlinks are already set up ([`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md)).

### Display / KMS

The touch browser uses **pygame + SDL KMS** (no full desktop required):

```bash
# Test manually (windowed — useful over SSH with desktop)
MPE_TOUCH_WINDOWED=1 ./scripts/start-touch-patch-browser.sh

# Fullscreen on the Pi console
./scripts/start-touch-patch-browser.sh
```

If the screen stays black, check:

```bash
ls /dev/fb* /dev/dri/*
journalctl -u touch-patch-browser -n 50
```

On Trixie (and recent Pi OS releases), your `config.txt` may need the vendor overlay for your specific panel (DSI) or HDMI `config.txt` timings. For sysfs brightness, add `dtoverlay=rpi-backlight` when supported. **HDMI 5" kits** sometimes have no software backlight — the slider will show unavailable; use the panel's physical control if present.

## Brightness

Controlled via Linux backlight sysfs:

```bash
# Quick test
echo 128 | sudo tee /sys/class/backlight/*/brightness
ls /sys/class/backlight/
```

The UI slider writes the same path and persists percent to `~/.patch_browser_brightness.json`.

**Permission fix (recommended once):**

```bash
# Allow the Pi user to set brightness without sudo for every drag
echo 'SUBSYSTEM=="backlight", RUN+="/bin/chmod 666 /sys/class/backlight/%k/brightness"' | \
  sudo tee /etc/udev/rules.d/99-backlight-permissions.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Without this rule, the slider may show “Brightness control unavailable” — power actions still use `sudo` like the encoder build.

## systemd service

Both browser units are installed by `configure-pi-paths.sh`. Which one **boots** is controlled by **`MPE_UI_MODE`** in `/etc/mpe/mpe.env`:

| `MPE_UI_MODE` | Enabled | Disabled |
|---------------|---------|----------|
| `oled` (default) | `patch-browser.service`, `boot-animation.service` | `touch-patch-browser.service` |
| `touch` | `touch-patch-browser.service`, `touch-boot-animation.service`, `mpe-shutdown-splash.service` | `patch-browser.service`, `boot-animation.service`, `shutdown-animation.service` |

On the SmartiPi Pi, set in `config/mpe.env` then reconfigure:

```bash
MPE_UI_MODE=touch
cd ~/MPE-Module
./scripts/configure-pi-paths.sh --local --force
systemctl is-enabled patch-browser touch-patch-browser
```

Only one browser UI should talk to Surge over OSC.

### Boot / restart — branded splash (no console flash)

On the SmartiPi DSI panel, **KMS/DRM keeps the last pygame frame** when `touch-patch-browser` stops or before the new process flips the display.

**Touch boot sequence** (`MPE_UI_MODE=touch`):

1. `touch-boot-animation.service` — starts before `getty@tty1`, claims DRM, loops the branded splash until the browser takes over.
2. `touch-patch-browser.service` **`ExecStartPre=prepare-dsi-display.sh`** — cooperative handoff + stop boot splash, kill stale pygame holders, wait for DRM release (prevents `kmsdrm not available` restart loops).
3. `start-touch-patch-browser.sh` — does **not** stop the splash or clear the framebuffer (that would release DRM and flash the console).
4. `touch_patch_browser.py` — cooperative handoff from the boot splash, paints boot splash immediately on kmsdrm, keeps an **indeterminate spinner** during patch scan, then draws the UI and signals ready (no percentage bar). On acquire failure, exits cleanly without orphaning DRM.

**Kernel console on DSI:** run once on the Pi (requires reboot):

```bash
sudo ./scripts/apply-dsi-cmdline (default keeps `console=tty1`; use `--strip-tty1` only with serial recovery).sh
```

This adds `console=serial0,115200 fbcon=map:0` to `/boot/firmware/cmdline.txt` and removes `console=tty1` when present so boot messages go to serial and fbcon stays off the panel. A timestamped backup is saved beside the original file.

**Calibration handoff:** the browser paints **Starting calibration…**, flips, and `exec`s `calibration_loader.py`. The loader paints on its first frame (`paint_immediate`) before heavy init. On exit it shows **Returning to patch browser…**, re-arms `touch-boot-animation`, then async-restarts the browser.

**Shutdown:** Power menu confirm starts `mpe-shutdown-splash.service`, then `systemctl poweroff` / `systemctl reboot`; the browser exits cleanly — splash is **not** held in-process. See [`SHUTDOWN.md`](SHUTDOWN.md). `mpe-shutdown-splash.service` uses Plymouth-like ordering (`Before=systemd-poweroff.service`, `TimeoutStopSec=infinity`) for UI and non-UI halt/reboot.

**Shutdown timing:** User services (Surge, browser, gadget) use bounded `TimeoutStopSec` so a stuck daemon cannot block poweroff for minutes. The shutdown splash unit is **not** bounded — it stays up until power is cut. After a test shutdown, on the next boot run `./scripts/shutdown-analyze-last.sh` to compare `Stopping`/`Stopped` lines in the previous boot journal and `/tmp/mpe-shutdown-splash.log`.

Implementation: `patch_browser/dsi_splash.py`, `touch_boot_splash.py`, `touch_shutdown_splash.py`, `scripts/prepare-dsi-display.sh`, `scripts/apply-dsi-cmdline.sh`. OLED builds keep `boot-animation.service` / `shutdown-animation.service`.

**Ready flag:** `/run/mpe-touch-browser-ready` is written after the browser's first full UI frame (post boot splash).

## Config

Same `/etc/mpe/mpe.env` as the encoder build:

| Variable | Purpose |
|----------|---------|
| `MPE_UI_MODE` | `oled` or `touch` — which patch browser systemd enables at boot |
| `MPE_BOOT_SPLASH_SECONDS` | Max in-app boot splash duration (default 3; min 1.2) |
| `MPE_FAVORITES_NAME` | Quick-access folder under Surge user patches |
| `MPE_TOUCH_WINDOWED` | Set `1` for windowed dev mode |

## Development on this repo

```bash
python3 -m py_compile touch_patch_browser.py patch_browser/backlight.py
MPE_TOUCH_WINDOWED=1 python3 touch_patch_browser.py
```

Spec: [`Documents/specs/touch-patch-browser-spec.md`](../Documents/specs/touch-patch-browser-spec.md)

## Mixer faders

The patch detail pane uses a **vertical fader strip** (mixing-board style) instead of a thin horizontal slider.

**Canon — three different zero semantics** (do not conflate Tail and Touch):

| Fader | Scale | At rest (no user override) | Double-tap reset |
|-------|-------|------------------------------|------------------|
| **Vol** | 0–100 (dB-linear) | Last volume | Default level |
| **Tail** | −50…+50 | **0** (patch-as-loaded) | **0** |
| **Touch** | −50…+50 | **Cal anchor** (maps `cal_floor` 0→0, max→+50) | **Cal anchor** |
| **Norm** | dB (e.g. −12…+24) | Calibrated gain | Calibrated gain |

Per-fader detail:

- **Vol** — drag the handle up/down (top = louder). Per-patch trim; persists to `~/.patch_browser_volume.json`. Display is **0–100** across fader travel with **dB-linear** mapping so normalized patches use the full range.
- **Tail** — per-patch multiplier on amp envelope **sustain, decay, and release** (both scenes). Under the hood: **0.25×–4.0×** via log mapping; fader shows **−50…+50** with **0** at center (patch-as-loaded). Double-tap resets to **0**. Persists in `~/.patch_browser_hold.json`.
- **Touch** — per-patch **MPE pressure floor** (light press vs full press). Fader shows **−50…+50**. **Handle position = cal anchor + user trim** (`touch_fader_value` in `patch_pressure.py`): calibration sets the anchor; drag applies trim (negative = less lift than cal). Stored as `cal_floor` + optional `user_touch_offset` in `~/.patch_browser_pressure.json`. Double-tap clears trim and restores the cal anchor. Live remapping: `mpe-pressure-remap.service`.
- **Norm** — per-patch normalization gain (dB); visible only when **Norm.** is checked. Double-tap resets to calibrated default.
- Touch **down + drag** on a fader; release does not trigger nav taps underneath.
- **Norm.** — label-left / checkbox-right toggle for per-patch loudness normalization (see [`PATCH_NORMALIZATION.md`](PATCH_NORMALIZATION.md)).

Brightness in **System settings** is a read-only row (current **%**); tap opens a modal with a large slider and presets (not mixed into the scrollable settings list).

## Touch input (evdev)

Scroll and tap use a **Linux evdev bridge** (`patch_browser/touch_evdev.py`) that reads the panel's `/dev/input/event*` device directly and forwards `SYN_REPORT` to pygame. SDL's synthetic touch events were unreliable on the SmartiPi stack (missed drags, scroll fighting fader grabs). Drag thresholds on list scroll vs mixer fader are tuned separately.

Set `MPE_TOUCH_EVDEV=0` to fall back to SDL-only input (debugging).

## Per-patch normalization

- **Norm.** — label-left / checkbox-right on the patch detail pane; persists per patch stem in `~/.patch_browser_normalization.json` (calibration data kept when toggling off). Greyed out when global normalization is off.
- **Calibrate missing patches** / **Force full re-calibration** — System settings (⋯) → confirm modal → loader on DSI. See **[PATCH_NORMALIZATION.md](PATCH_NORMALIZATION.md)**.

**Calibration handoff:** launching calibration from the touch browser sets `MPE_CALIB_FROM_BROWSER=1` before `exec` into `calibration_loader.py` (direct Python exec — no bash gap). Teardown must not stop `touch-patch-browser` synchronously (same systemd main process) and schedules an async restart instead. Constant and invariant live in `patch_browser/calibration_constants.py` and `calibration_teardown.py`.

## System settings (⋯)

Right-side **slide-out panel** (tap **⋯**, tap outside the panel, swipe right, or **×** to close). Centered modals (Theme, Wi‑Fi, brightness, audio pickers, power, calibration confirm) also close when you tap the dimmed backdrop outside the panel. The settings body and Wi‑Fi network list show **edge fades** when more rows are off-screen (standard scroll-overflow cue). Scrollable body; **Power…** fixed at the bottom with a divider. Row buttons and toggles activate on **finger up** (same tap-vs-scroll thresholds as the patch list) so you can scroll without triggering rows under your finger. Confirm modals (calibration, power) use the same up-to-activate pattern.

UI preferences persist in `~/.patch_browser_ui.json` (see [UI theme](#ui-theme-system-settings--theme)).

Sections are grouped top-to-bottom:

### Sound

- **Audio…** — drill-in sub-screen (chevron shows live summary: profile · buffer · sample rate).
  - **USB Audio** profile toggle (Analog vs USB host); header badge matches. Background switch with overlay — see **[USB-AUDIO-HOST.md](USB-AUDIO-HOST.md)**.
  - **Buffer** — preset picker (32–2048 samples) + approximate latency; restarts Surge ([#44](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/44)).
  - **Sample rate** — **44.1 kHz / 48 kHz** picker; persists to `/etc/mpe/mpe.env` and restarts Surge (+ USB gadget when **usb-host**) ([#39](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/39)).
  - **Dynamic voice limit** — poly governor toggle.
  - **Patch normalization** — master toggle for all per-patch Norm controls.
  - **Calibrate missing patches** / **Force full re-calibration** — confirm modal → loader on DSI. See **[PATCH_NORMALIZATION.md](PATCH_NORMALIZATION.md)**.

Tapping the header **Analog/USB** badge opens settings directly on this Audio sub-screen.

### Display

- **Brightness** — shows current **%**; tap opens a modal slider + presets (25/50/75/100, Reset).
- **Theme…** — base theme, accent style, accent color. See [UI theme](#ui-theme-system-settings--theme).

### Network

- **Wi‑Fi** — current SSID subtitle; tap to scan/connect via NetworkManager (venue recovery, no SSH).

### Advanced (expand/collapse — full-row tap target)

- **CPU meter** — toggle header bar meter (default on).
- **Restart Surge** — when status is not healthy.

**Header CPU meter** (when enabled): compact bar left of **⋯**. Polls ~5 Hz on a background thread. Uses **`/proc` CPU time for `surge-xt-cli`** (Surge has no documented CPU OSC). Semantic green → yellow → red even in Monochrome accent style.

## UI theme (System → Theme…)

*Last updated: 2026-08-01 (America/Toronto)*

The touch UI uses a **single live theme system** in `patch_browser/ui_theme.py`. Accent, text, and muted colors are module-level knobs updated from saved preferences; `Theme` surface tokens (background tiers, overlays) come from the selected **base theme**.

**Where it applies:** patch browser, **boot splash**, **shutdown splash**, and **calibration loader** all call `reload_theme_from_prefs()` at paint time so load/return screens match the active accent and style.

### Flow

1. **Theme** modal — base theme, accent style, accent preview + **Choose color…**, **Done** / **Cancel**
2. **Accent color** screen — preset swatches, **Saved** custom colors (× to delete), **+** custom picker, **Back**
3. **Custom color** picker — live preview, **Red / Green / Blue** sliders, **Save** (primary), **Delete** (when editing a saved color), **Back**

Changes preview live in the modal; **Done** on the Theme screen writes theme prefs. **Save** on the custom picker writes to the saved palette immediately.

### Base theme

| Value | Label | Surfaces |
|-------|-------|----------|
| `standard` | Original dark | Raised gray canvas (`#0A0A0C` family) |
| `oled_black` | OLED dark | True-black content + tiered elevation (see table below) |

Persists as `theme_mode` in `~/.patch_browser_ui.json`.

### Accent style

| Value | Label | Behavior |
|-------|-------|----------|
| `monochrome` | Monochrome | Labels, chrome, heart (favorited), and most UI accents use the chosen accent color |
| `minimal` | Minimal accent | Near-white/gray **text** and **muted**; accent color on interactive chrome only (sliders, checkbox fills, primary buttons) |

Legacy pref `accent_style: "full"` loads as `monochrome`.

**Exceptions (always accent-colored):** patch **loaded** dot in the nav list (`theme.accent`); **ok** success text (e.g. calibration done). Regardless of accent style.

**Exceptions (semantic, not accent):** **danger** destructive labels and confirms (red); **CPU meter** bar (green → yellow → red).

### Accent color

- **Presets:** Purple, Blue, Violet, Teal, Amber, Rose (`ACCENT_PRESETS` in code)
- **Saved custom colors:** up to 12 entries in `custom_accent_colors` (each `{ "id", "name", "rgb" }`)
- **Custom picker:** RGB sliders; **Save** adds/updates palette; **×** on a saved swatch deletes

Persists as `accent_rgb` (3 integers) and `custom_accent_colors` in `~/.patch_browser_ui.json`.

Example:

```json
{
  "theme_mode": "oled_black",
  "accent_style": "monochrome",
  "accent_rgb": [127, 27, 228],
  "custom_accent_colors": [
    { "id": "a1b2c3d4", "name": "#ff0088", "rgb": [255, 0, 136] }
  ],
  "show_cpu_meter": true
}
```

### Modal button hierarchy

One **primary** (accent fill) commit button per dialog; dismiss actions stay neutral.

| Role | Visual | Examples |
|------|--------|----------|
| Primary | Accent fill | Done, Save, Start |
| Dismiss | `surface_alt` | Cancel, Back |
| Destructive | Danger fill / label | Confirm shutdown, Delete |
| Secondary | `surface_alt` row | Choose color…, Power menu rows (Shutdown/Restart use danger **text**) |

### OLED dark surface tiers

OLED mode follows the usual **Material / iOS dark** pattern: **tiered surfaces + subtle elevation**, not hard outlines. Overlays use a **~50% black backdrop dim**; panels sit on a brighter surface tier so they read above true-black content.

**Status indicators** (header **USB** / **Analog** badges, CPU label): **text only — no box, border, or outline.** Order left→right among readouts: **USB/Analog** · **CPU** · **⋯**. Patch title stays on the left.

| Token | Role | Before (flat) | After (tiered) |
|-------|------|---------------|----------------|
| `bg` | Canvas / main content | `#000000` | `#000000` (unchanged — OLED power) |
| `surface` | 1dp — status bar, nav | `#000000` | `#060608` |
| `surface_elevated` | 2dp — settings panel, modals | *(same as surface)* | `#0A0A0E` |
| `surface_content` | Patch detail pane | *(same as surface)* | `#000000` |
| `surface_alt` | Row hover / selected | `#121216` | `#0E0E12` |
| Overlay | Backdrop behind panels/modals | mixed 120–200 α | `#000000` @ 50% α |
| Hairline | Optional header separator | none | `#FFFFFF` @ ~9% (header bottom only) |

Implementation: `patch_browser/ui_theme.py` (`OLED_BLACK_THEME`, `apply_theme_preferences`, `reload_theme_from_prefs`); elevated panels get an optional **1px top highlight** (light falloff, not a border). Tests: `tests/test_ui_theme.py`, `tests/test_touch_browser_smoke.py`.

## All patches view (#10)

From the folder list, tap **All** in the left nav header:

- **Flat list** of every scanned patch, sorted A→Z
- **Folder name** shown as a subtitle on each row (not used for navigation)
- **♥ / ♡** — filled heart if the patch is already in Quick Access (`MPE_FAVORITES_NAME`); indicator only (toggle still on patch detail after load)
- **A–Z rail** on the right — tap a letter to jump scroll to that section
- Tap a patch → loads in Surge and shows the detail pane (Vol, Tail, Touch, Norm.)
- **Up** returns to the folder list

Spec: [`Documents/specs/touch-patch-browser-browse-ux-spec.md`](../Documents/specs/touch-patch-browser-browse-ux-spec.md)

Epic (instruments, favorites v2, nested nav): [`Documents/specs/touch-browser-instruments-favorites-spec.md`](../Documents/specs/touch-browser-instruments-favorites-spec.md)

## Browse navigation transitions (#24)

All left-nav mode changes go through `_enter_nav_mode()` in `patch_browser/touch_browser_nav.py`. It owns:

- `left_nav_mode` and optional `browse_folder_index` / `left_nav_collapsed`
- A–Z rail capture cleanup when leaving All patches
- All-patches scroll snapshot / restore
- `_relayout()` when geometry changes (enter/leave All patches); otherwise `_update_nav_list_geometry()` + `_refresh_lists()`

| From | Action | To | Notes |
|------|--------|-----|-------|
| FOLDERS | Tap folder row | PATCHES | List scroll reset to top |
| FOLDERS / PATCHES | Tap **All** | ALL_PATCHES | Nav widens; main detail hidden (`main_rect.w = 0`) |
| ALL_PATCHES | Tap **◀** | FOLDERS | Scroll position saved for next All visit |
| PATCHES | Tap **◀** (at category root) | FOLDERS | Top-level folder list |
| PATCHES | Tap **◀** (inside subfolder) | PATCHES | Back one level (`browse_inner_segments` pop) |
| PATCHES | Tap subfolder row (`Name  >`) | PATCHES | Drill into subfolder; list scroll reset |
| ALL_PATCHES | Tap patch row | PATCHES | Load patch; restore normal two-pane layout |
| ALL_PATCHES | Tap **Current** | PATCHES | Jump to loaded patch's folder |
| PATCHES | Tap **Current** | PATCHES | Jump browse index to loaded folder |

Tests: `tests/test_touch_browser_nav_transitions.py`

## Patch index (Phase 0.5)

`PatchScanner` indexes every `.fxp` by **full path** (no silent same-name collisions). Each patch dict includes:

| Field | Purpose |
|-------|---------|
| `stable_key` | `factory:Bass/Sub/Lead 1` — canonical identity |
| `inner_segments` | Nested folders under top-level category |
| `relative_path` | Path from patch root |

Nested folder API: `get_subfolders()`, `get_patches_in_folder()`, `folder_tree`. Browse state: `browse_folder_index` + `browse_inner_segments` (nav stack within a category).

All-patches row subtitles show `Category/Sub/...` when nested.

Tests: `tests/test_patch_scanner.py`, `tests/test_patch_identity.py`

## Instrument metadata (Phase 1)

Each patch dict also gets `instruments` (list) and `instrument_primary` after scan.

| Source | File |
|--------|------|
| Shipped baseline | `data/patch_metadata_baseline.json` (regenerate with `scripts/build-patch-metadata-baseline.py` on a machine with the full library) |
| User overrides | `~/.patch_browser_metadata.json` (`instrument_user` per `stable_key`) |
| On-device fallback | Heuristic classifier in `patch_browser/patch_metadata.py` when no baseline row exists |

Instrument chips UI lands in Phase 4; metadata is attached at scan time now.

Tests: `tests/test_patch_metadata.py`, `tests/test_patch_scanner_metadata.py`

## Instrument chips (Phase 4)

When browsing a **folder** or **All patches**, a horizontal chip row appears under the nav header:

- **All** clears the instrument filter; other chips (Piano, Pad, Bass, **Other**, …) narrow the list
- In folder browse, chips reflect instruments in the **current folder subtree**
- Drag horizontally to scroll chips when they overflow; tap to filter
- All-patches row subtitle shows the top-level **category** only; folder browse rows are name-only

Tests: `tests/test_instrument_filter.py`

## Long-press menus (Phase 5)

Hold a folder or patch row in the left nav for ~600ms (cancel if you scroll first):

| Target | Actions |
|--------|---------|
| Library folder | Add all to Liked; Add all to folder… |
| Patch | Add/remove Quick Access; Move to folder…; Set instrument… |
| Quick Access subfolder | New subfolder; Rename / Delete (top-level user folders only) |

Folder and instrument pickers are sub-menus. New/rename folder uses the on-screen keyboard (same layout as Wi‑Fi password entry).

Tests: `tests/test_context_menu.py`

## Favorites v2 (Phase 3)

Quick Access favorites use a **copy-based** layout plus a JSON index — not symlinks, never deleting source patches.

| Piece | Location |
|-------|----------|
| On-disk copies | `Quick Access/<folder>/` (default **`Liked/`**) |
| Index | `~/.patch_browser_favorites.json` (override: `MPE_FAVORITES_INDEX_FILE`) |
| Identity | `stable_key` per patch (see Patch index above) |

**Heart toggle** (patch detail): copies the patch into `Liked/` and writes an index entry keyed by `stable_key`. Unfavorite removes the copy and index row only. After a toggle, the scanner rescans the Quick Access subtree only — not the full library.

**User folders:** the index API supports create/rename/delete folders under Quick Access. UI for folder management is Phase 5 (long-press menus); hearts always land in **Liked** for now.

**All patches view:** ♥ indicates a patch is indexed (any Quick Access folder).

### Migrate flat Quick Access → Liked

Older installs may have `.fxp` copies sitting directly in the Quick Access root. Run on the Pi (or any machine with the library mounted):

```bash
cd ~/MPE-Module
python3 scripts/migrate-favorites-v2.py --dry-run
```

Dry-run lists each root copy, its proposed `Liked/` target, and the resolved `stable_key`. Migration **aborts** if any copy cannot be matched to exactly one library patch (ambiguous stem). Quick Access copies are excluded from the stem map so they do not collide with factory/user sources.

When the plan looks correct:

```bash
python3 scripts/migrate-favorites-v2.py --apply
# optional backup before apply:
python3 scripts/migrate-favorites-v2.py --apply --backup-dir ~/qa-migration-backup
```

**Rollback:** if you used `--backup-dir`, restore the backed-up Quick Access tree and `~/.patch_browser_favorites.json` from that directory. Without a backup, move files manually from `Quick Access/Liked/` back to the Quick Access root and remove or edit the JSON index.

Tests: `tests/test_favorites_index.py`, `tests/test_migrate_favorites_v2.py`

## Known gaps (v0)

- Prefix/text search and folder chips not implemented (All patches + A–Z first)
- Portrait panels are unsupported for this rig (yours is landscape)
- Very large patches (e.g. **Bowed String**, ~8 MB) may need a calibration retry — use `--patch "Bowed String"` or re-run loader with `--force`
