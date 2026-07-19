# Patch Browser UI — controls and configuration

One encoder (rotate + push button) and a 1.3" OLED. This doc is the reference for **gestures**, **config**, and honest limits. Hold timings are in `patch_browser_ui.py` (`Config` dataclass); folder name is **`MPE_FAVORITES_NAME`** in `/etc/mpe/mpe.env`.

## Honest status

The on-device UI is **not good yet**. Scrolling is unreliable — missed steps and double steps are common on the reference KY-040. The debounce stack mostly **removed false presses** (ghost scrolls when you touch the button, accidental short taps firing actions). It did **not** make navigation feel tight or predictable.

**There is no normal click/tap.** Short button releases are deliberately discarded. Every intentional button action is a **timed hold**.

## UI controls (gestures)

| Input | Action |
|---|---|
| **Rotate encoder** | Scroll categories (category mode) or patches within one category (patch mode) |
| **< 0.5s button (tap)** | **Ignored** — too unreliable on KY-040 |
| **0.5s – 2s hold + release** | **Toggle mode** — category ↔ patch. Aim **~1s** in practice |
| **2s+ hold + release** | **Copy current patch to quick-access folder** — confirm dialog; bold hold (~1s) again for Yes/No. **Unreliable** — overlaps mode-toggle window; PC workflow is safer ([`PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md)) |
| **8s+ hold** | **Power menu** — shutdown/restart ([`POWER_BUTTON_SETUP.md`](POWER_BUTTON_SETUP.md)) |
| **Stop scrolling ~1.25s** (patch mode) | **Load patch** — no separate "select" click |

There is no separate Enter / Back button today — mode toggle is one gesture both ways. **Planned:** enter (~1s) and back (~3s) as distinct holds; **second encoder** on the hardware roadmap.

## Configuration

Set on the **Pi** in `/etc/mpe/mpe.env` (written by `configure-pi-paths.sh --local --force` from `config/mpe.env` + repo defaults). `patch-browser.service` loads this via `EnvironmentFile`.

| Variable | Default | What it controls |
|---|---|---|
| **`MPE_FAVORITES_NAME`** | `!Quick Access` | **Folder name** under `~/Documents/Surge XT/Patches/` — use a leading **`!`** so it sorts first in Surge and on the device. Used for pinned category + on-device long-press copy target. |

**Examples:**

```bash
# /etc/mpe/mpe.env on Pi
MPE_FAVORITES_NAME="!Quick Access"   # default — folder on disk includes !
# MPE_FAVORITES_NAME=Mitch           # reference build (browser shows !Mitch)
# MPE_FAVORITES_NAME="!Gig Bag"      # any name; leading ! recommended
```

After changing:

```bash
cd ~/MPE-Module
./scripts/configure-pi-paths.sh --local --force
sudo systemctl restart patch-browser
```

Copy template from [`config/mpe.env.example`](../config/mpe.env.example). Full path reference: [`PATHS.md`](PATHS.md).

### Quick-access folder (what the config names)

Factory/third-party categories sort A–Z — hundreds of them. Without the `!` display prefix, a user folder named `Favorites` would be hard to find while scrolling.

| Layer | Path / label |
|---|---|
| **On disk (Surge user patches)** | `~/Documents/Surge XT/Patches/<MPE_FAVORITES_NAME>/` |
| **Browser category list** | Same name — if it lacks `!`, the UI adds one for display only |
| **Long-press copy target** | Same folder on disk |

**Default:** create **`!Quick Access`** in Surge XT (bang in the folder name). That pins it at the top in both Surge's browser and on the Pi without extra config.

**Two ways to fill the folder:**

1. **PC (recommended)** — create/rename folder in Surge XT, add patches, deploy ([`PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md)).
2. **On-device (experimental)** — while browsing a patch, **hold 2s+** → `Copy to !<name>?` dialog → bold hold to confirm Yes. Works sometimes; encoder timing makes this frustrating.

The folder is created automatically on first copy if it doesn't exist.

## Load debounce

In patch mode, each rotation restarts a **1.25s timer** (`LOAD_DEBOUNCE_TIME`). Surge loads the highlighted patch only after you **stop scrolling** for that interval.

## Next major upgrades

- **Button model:** separate enter (~1s) and back (~3s) holds — [`ENCODER_BUTTON_REVIEW.md`](../ENCODER_BUTTON_REVIEW.md)
- **Hardware:** second encoder (scroll vs confirm) — not in v1 BOM

## Why it's built this way (for now)

Cheap KY-040 + one GPIO button + [`ENCODER_NO_VCC.md`](ENCODER_NO_VCC.md) wiring. See [`REFERENCE_BOM.md`](../REFERENCE_BOM.md) — encoder listed as **weak recommendation**.
