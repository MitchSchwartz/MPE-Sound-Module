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
your-user ALL=(ALL) NOPASSWD: /sbin/poweroff, /sbin/reboot, /bin/systemctl
```

See [`docs/POWER_BUTTON_SETUP.md`](POWER_BUTTON_SETUP.md) for the encoder Pi pattern.

## Design goals

- **Browser is home:** patch list + detail on one screen; no separate playing mode
- **Non-disruptive:** dark theme, minimal chrome
- **Every feature on-screen:** browse, settings, brightness, power (no encoder)
- **Borrowed patterns:** `PatchScanner`, `PatchLoader`, `SurgeMonitor`, favorites folder, last-patch restore from `patch_browser_ui.py`

Interaction model:

| **Left nav (expanded)** | **Patches** in current folder, or **Folders** after Up | Tap patch → load + detail on main |
| **Up** | Switch left nav to folder list | Does not load anything |
| **Current** | When browsing another folder — jump to loaded patch's folder + patch list | |
| **< collapse** | Nav hides; `>` tab remains to expand | Main detail gets full width |
| **Main (right)** | Selected patch: **Vol** fader + **Norm.** toggle | No back button — list is always on the left |

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
| `touch` | `touch-patch-browser.service` | `patch-browser.service`, `boot-animation.service` |

On the SmartiPi Pi, set in `config/mpe.env` then reconfigure:

```bash
MPE_UI_MODE=touch
cd ~/MPE-Module
./scripts/configure-pi-paths.sh --local --force
systemctl is-enabled patch-browser touch-patch-browser
```

Only one browser UI should talk to Surge over OSC.

### Boot / restart — no stale UI flash

On the SmartiPi DSI panel, **KMS/DRM keeps the last pygame frame** when `touch-patch-browser` stops or before the new process flips the display. That frame can be an **older build** (e.g. pre-`d66ef3a` with four stub faders Cut/Res/Snd/Vol) until the current app draws.

**Ruled out on `raspberrypi2` (2026-07-31):** `boot-animation.service` and `patch-browser.service` are **disabled**; `ExecStart` points at `~/MPE-Module/touch_patch_browser.py` (single Vol fader at `8fca259`); no second service races the DSI.

**Fix:** `scripts/clear-dsi-framebuffer.py` runs from `start-touch-patch-browser.sh` before Python import; `touch_patch_browser.py` fills the background and flips immediately after `set_mode()`.

## Config

Same `/etc/mpe/mpe.env` as the encoder build:

| Variable | Purpose |
|----------|---------|
| `MPE_UI_MODE` | `oled` or `touch` — which patch browser systemd enables at boot |
| `MPE_FAVORITES_NAME` | Quick-access folder under Surge user patches |
| `MPE_TOUCH_WINDOWED` | Set `1` for windowed dev mode |

## Development on this repo

```bash
python3 -m py_compile touch_patch_browser.py patch_browser/backlight.py
MPE_TOUCH_WINDOWED=1 python3 touch_patch_browser.py
```

Spec: [`Documents/specs/touch-patch-browser-spec.md`](../Documents/specs/touch-patch-browser-spec.md)

## Mixer faders

The patch detail pane uses a **vertical fader strip** (mixing-board style) instead of a thin horizontal slider:

- **Vol** — drag the handle up/down (top = louder). Persists to `~/.patch_browser_volume.json` and sends OSC via `PatchLoader.set_volume`.
- Touch **down + drag** on the fader; release does not trigger nav taps underneath.
- **Norm.** — label-left / checkbox-right toggle for per-patch loudness normalization (see [`PATCH_NORMALIZATION.md`](PATCH_NORMALIZATION.md)).

Brightness in **System settings** still uses a horizontal slider (one-off control, not live mixing).

## Touch input (evdev)

Scroll and tap use a **Linux evdev bridge** (`patch_browser/touch_evdev.py`) that reads the panel's `/dev/input/event*` device directly and forwards `SYN_REPORT` to pygame. SDL's synthetic touch events were unreliable on the SmartiPi stack (missed drags, scroll fighting fader grabs). Drag thresholds on list scroll vs mixer fader are tuned separately.

Set `MPE_TOUCH_EVDEV=0` to fall back to SDL-only input (debugging).

## Per-patch normalization

- **Norm.** — label-left / checkbox-right on the patch detail pane; persists per patch stem in `~/.patch_browser_normalization.json` (calibration data kept when toggling off). Greyed out when global normalization is off.
- **Calibrate missing patches** / **Force full re-calibration** — System settings (⋯) → confirm modal → loader on DSI. See **[PATCH_NORMALIZATION.md](PATCH_NORMALIZATION.md)**.

## System settings (⋯)

Right-side **slide-out panel** (tap **⋯**, tap outside, swipe right, or **×** to close). Scrollable body; **Power…** fixed at the bottom with a divider. Row buttons and toggles activate on **finger up** (same tap-vs-scroll thresholds as the patch list) so you can scroll without triggering rows under your finger. Confirm modals (calibration, power) use the same up-to-activate pattern.

UI preferences persist in `~/.patch_browser_ui.json` (e.g. `show_cpu_meter`, `theme_mode`: `"standard"` or `"oled_black"`).

- **CPU meter** — toggle show/hide for the header bar (not the numeric overlay; bar-only meter). Default on.
- **Patch normalization** — master toggle for all per-patch Norm. controls (persists in `~/.patch_browser_normalization.json` under `_global`; per-patch flags unchanged when off).
- **Surge status** — `SurgeMonitor` probes the CLI process, OSC port 53280, and recent log lines. Stale PIDs and historical audio-device errors in `surge-cli.log` no longer show as false *down* (fixed 2026-07-30).
- **Header CPU meter** — compact bar to the left of the **⋯** settings button when enabled. Polls at ~5 Hz on a background thread (UI stays responsive). Surge XT does **not** document a CPU OSC address (`/q/cpu`, `/cpu`, `/status/cpu` are probed speculatively when OSC out is enabled). The meter therefore uses **`/proc` CPU time for the `surge-xt-cli` process** as a live-play diagnostic — same green → yellow → red thresholds as a DAW meter. Shows **—** when Surge is offline. This approximates audio-engine stress on a dedicated Pi; it is not identical to Surge’s internal VU *Show CPU Usage* ratio (audio callback time ÷ buffer time), which is GUI-only today.
- **Restart Surge** — shown when status is not healthy; uses the same systemd unit as the encoder build.
- **Calibrate missing patches** — incremental run over the full scanned library (patches without `gain_db` only).
- **Force full re-calibration** — re-measures every patch in the scan tree (`--force`). See [Per-patch normalization](#per-patch-normalization).

## OLED black theme

Toggle in System settings → **OLED black**. Persists as `theme_mode: "oled_black"` in `~/.patch_browser_ui.json`. Standard theme is unchanged.

OLED mode follows the usual **Material / iOS dark** pattern: **tiered surfaces + subtle elevation**, not hard outlines. Overlays use a **~50% black backdrop dim**; panels sit on a brighter surface tier so they read above true-black content.

| Token | Role | Before (flat) | After (tiered) |
|-------|------|---------------|----------------|
| `bg` | Canvas / main content | `#000000` | `#000000` (unchanged — OLED power) |
| `surface` | 1dp — status bar, nav | `#000000` | `#060608` |
| `surface_elevated` | 2dp — settings panel, modals | *(same as surface)* | `#0A0A0E` |
| `surface_content` | Patch detail pane | *(same as surface)* | `#000000` |
| `surface_alt` | Row hover / selected | `#121216` | `#0E0E12` |
| Overlay | Backdrop behind panels/modals | mixed 120–200 α | `#000000` @ 50% α |
| Hairline | Optional header separator | none | `#FFFFFF` @ ~9% (header bottom only) |

Implementation: `patch_browser/ui_theme.py` (`theme_oled_black()` / `OLED_BLACK_THEME`); elevated panels get an optional **1px top highlight** (light falloff, not a border). Calibration loader inherits the same tokens when launched from the browser.

## Known gaps (v0)

- Search/filter across 3000+ patches not implemented (scroll lists first)
- Portrait panels are unsupported for this rig (yours is landscape)
- Boot/shutdown animations still target the 1.3" OLED service
- Very large patches (e.g. **Bowed String**, ~8 MB) may need a calibration retry — use `--patch "Bowed String"` or re-run loader with `--force`
