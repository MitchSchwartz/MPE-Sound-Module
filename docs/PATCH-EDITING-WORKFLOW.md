# Surge XT Patch Editing Workflow

Complete guide for editing Surge XT patches on your PC and deploying them to your Raspberry Pi device.

## Two repos

| Repo | Role |
|------|------|
| **MPE-Module** | Code, docs, deploy scripts (this repo) |
| **Your assets repo** | Private backup: `assets/user-data/Patches/`, optional factory/third-party copy, binary |

Clone both as siblings under the same parent folder. Scripts resolve the assets repo automatically (`../mpe-assets`, `../MPE-Library`, or `../MPE-Personal`), or set `MPE_PERSONAL_REPO`. Full path reference: **[PATHS.md](PATHS.md)**.

## Overview

1. Edit patches in Surge XT on your PC using the native GUI
2. Changes land in **your assets repo** via symlinks → commit there
3. Deploy from **MPE-Module** scripts (~5–10 seconds)
4. Test on your MPE controller

## Quick-access folder (live set)

The Pi browser pins **one user patch folder** at the top of the category list. Default folder name: **`!Quick Access`** (the `!` is part of the folder name in Surge — it sorts first). Override with **`MPE_FAVORITES_NAME`** in `/etc/mpe/mpe.env`.

**PC workflow (recommended):**

1. In Surge XT, create `~/Documents/Surge XT/Patches/!Quick Access/` (or your custom name — leading `!` recommended).
2. Save or copy patches into that folder.
3. Deploy with the steps below.

On-device copy via encoder hold is **disabled** — it overlapped mode toggle. Use the PC workflow above.

See [`PATCH_BROWSER_UI.md`](PATCH_BROWSER_UI.md) for the full controls + config table.

## One-Time Setup

### Step 1: Create PC symlinks

From **MPE-Module** (with your **assets repo** cloned beside it):

```bash
cd MPE-Module
./scripts/setup-windows-symlinks.sh
```

Optional overrides (see `config/mpe.env.example`):

```bash
export MPE_PERSONAL_REPO="../mpe-assets"
export SURGE_XT_DIR="$HOME/Documents/Surge XT"
./scripts/setup-windows-symlinks.sh
```

This junctions Surge XT's `Patches` folder → `../mpe-assets/assets/user-data/Patches` (or your path).

### Step 2: Verify Setup

Open Surge XT → Patch Browser → you should see your custom folders (e.g. `Live/`).

## Daily Editing Workflow

### 1. Edit Patches on PC

Save patches under your custom folder in Surge XT. Files land in the assets repo via the symlink.

### 2. Review and Commit Changes

Commit in **your assets repo** (not MPE-Module):

```bash
cd ../mpe-assets
git status
git add assets/user-data/Patches/
git commit -m "Update MyPatch.fxp: added chorus"
git push
```

### 3. Deploy to Pi

**Option A — direct deploy (default):**

```bash
cd ../MPE-Module
./scripts/deploy-patches.sh
```

Set `PI_HOST`, `PI_USER`, `SSH_KEY` in `config/mpe.env` if needed (see PATHS.md).

**Option B — Pi symlinks to assets repo:** commit/push assets repo, then on the Pi:

```bash
cd ~/mpe-assets && git pull
sudo systemctl restart surge-xt-cli patch-browser
```

(Use your actual clone path if not in `$HOME` — see PATHS.md.)

### 4. Test on Pi

Play your controller; load the patch from the on-device browser.

## Pi setup / moving repos

When you first set up the Pi — or if clone paths change — reconfigure:

1. Clone **MPE-Module** and your **assets repo** (default: both under `$HOME`)
2. `./scripts/configure-pi-paths.sh` — writes `/etc/mpe/mpe.env`, installs systemd units
3. `./scripts/setup-pi-symlinks.sh` — points Surge patch dirs at assets repo
4. Restart services

Details: **[PATHS.md](PATHS.md)** § Pi setup.

## Advanced Operations

### Browse Factory Patches for Reference

Backup copies (optional) in your assets repo:

- `../mpe-assets/assets/patches/patches_factory`
- `../mpe-assets/assets/patches/third-party/patches_3rdparty`

Or use the factory library from a normal [Surge XT](https://surge-synthesizer.github.io/) install.

To start from a factory patch: copy into `assets/user-data/Patches/YourFolder/`, rename, edit in Surge XT.

### Sync Changes from Pi to PC

```bash
cd MPE-Module
./scripts/sync-from-device.sh
cd ../mpe-assets
git add -A && git commit -m "Sync from device $(date +%Y-%m-%d)"
```

### Full System Deployment (Disaster Recovery)

```bash
cd MPE-Module
./scripts/deploy-all.sh
```

Deploys binary, patch libraries, configs, and services (~3–6 min).

## File Locations Reference

Paths below use `$HOME` — your OS user home (Windows: Git Bash `$HOME` ≈ `~/Documents` parent).

### PC

| What | Path |
|------|------|
| Code repo | `MPE-Module/` (this clone) |
| Assets repo | `../mpe-assets/` (or `MPE_PERSONAL_REPO`) |
| Surge XT user data | `$SURGE_XT_DIR` (default: `$HOME/Documents/Surge XT`) |
| Custom patches (symlink) | `$SURGE_XT_DIR/Patches/` |
| Custom patches (git) | `../mpe-assets/assets/user-data/Patches/` |

### Pi

| What | Path |
|------|------|
| Code repo | `$HOME/MPE-Module` (override: `MPE_MODULE_REPO`) |
| Assets repo | `$HOME/mpe-assets` (override: `MPE_PERSONAL_REPO`) |
| Surge CLI binary | `$HOME/surge/build/surge_xt_products/surge-xt-cli` |
| Factory / 3rd-party | `$HOME/surge/resources/data/patches_*` (often symlinked to assets repo) |
| Custom patches | `$HOME/Documents/Surge XT/Patches/` |
| Surge log | `$HOME/surge-cli.log` |

## Quick Reference

```bash
# One-time PC setup
cd MPE-Module && ./scripts/setup-windows-symlinks.sh

# Daily
cd ../mpe-assets && git add assets/user-data/Patches/ && git commit -m "patches"
cd ../MPE-Module && ./scripts/deploy-patches.sh

# Pi path (re)configuration
./scripts/configure-pi-paths.sh
./scripts/setup-pi-symlinks.sh

# Weekly backup
./scripts/sync-from-device.sh
```

Happy patching!
