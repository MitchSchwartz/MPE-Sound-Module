# Surge XT Patch Editing Workflow

Complete guide for editing Surge XT patches on Windows and deploying them to your Raspberry Pi device.

## Two repos

| Repo | Role |
|------|------|
| **MPE-Module** | Code, docs, deploy scripts (this repo) |
| **MPE-Personal** | Private backup: `assets/user-data/Patches/`, factory/third-party copy, binary |

Clone both as siblings under the same parent folder. Scripts resolve `../MPE-Personal` automatically, or set `MPE_PERSONAL_REPO`. Full path reference: **[PATHS.md](PATHS.md)**.

## Overview

1. Edit patches in Surge XT on Windows using the native GUI
2. Changes land in **MPE-Personal** via symlinks → commit there
3. Deploy from **MPE-Module** scripts (~5–10 seconds)
4. Test on your MPE controller

## One-Time Setup

### Step 1: Create Windows Symlinks

From **MPE-Module** (with **MPE-Personal** cloned beside it):

```bash
cd MPE-Module
./scripts/setup-windows-symlinks.sh
```

Optional overrides (see `config/mpe.env.example`):

```bash
export MPE_PERSONAL_REPO="../MPE-Personal"
export SURGE_XT_DIR="$HOME/Documents/Surge XT"
./scripts/setup-windows-symlinks.sh
```

This junctions Surge XT's `Patches` folder → `../MPE-Personal/assets/user-data/Patches`.

### Step 2: Verify Setup

Open Surge XT → Patch Browser → you should see your custom folders (e.g. `Mitch/`).

## Daily Editing Workflow

### 1. Edit Patches in Windows

Save patches under your custom folder in Surge XT. Files land in MPE-Personal via the symlink.

### 2. Review and Commit Changes

Commit in **MPE-Personal** (not MPE-Module):

```bash
cd ../MPE-Personal
git status
git add assets/user-data/Patches/
git commit -m "Update Church - Mod.fxp: added chorus"
git push
```

### 3. Deploy to Pi

**Option A — direct deploy (default):**

```bash
cd ../MPE-Module
./scripts/deploy-patches.sh
```

Set `PI_HOST`, `PI_USER`, `SSH_KEY` if needed (see PATHS.md).

**Option B — Pi symlinks to MPE-Personal:** commit/push MPE-Personal, then on the Pi:

```bash
cd ~/MPE-Personal && git pull
sudo systemctl restart surge-xt-cli patch-browser
```

(Use your actual clone path if not in `$HOME` — see PATHS.md.)

### 4. Test on Pi

Play your controller; load the patch from the on-device browser.

## Pi setup / moving repos

When you first set up the Pi — or if clone paths change — reconfigure:

1. Clone **MPE-Module** and **MPE-Personal** (default: both under `$HOME`)
2. `./scripts/configure-pi-paths.sh` — writes `/etc/mpe/mpe.env`, installs systemd units
3. `./scripts/setup-pi-symlinks.sh` — points Surge patch dirs at MPE-Personal
4. Restart services

Details: **[PATHS.md](PATHS.md)** § Pi setup.

## Advanced Operations

### Browse Factory Patches for Reference

Backup copies (optional) in MPE-Personal:

- `../MPE-Personal/assets/patches/patches_factory`
- `../MPE-Personal/assets/patches/third-party/patches_3rdparty`

Or use the factory library from a normal [Surge XT](https://surge-synthesizer.github.io/) install.

To start from a factory patch: copy into `assets/user-data/Patches/Mitch/`, rename, edit in Surge XT.

### Sync Changes from Pi to PC

```bash
cd MPE-Module
./scripts/sync-from-device.sh
cd ../MPE-Personal
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
| Personal repo | `../MPE-Personal/` |
| Surge XT user data | `$SURGE_XT_DIR` (default: `$HOME/Documents/Surge XT`) |
| Custom patches (symlink) | `$SURGE_XT_DIR/Patches/` |
| Custom patches (git) | `../MPE-Personal/assets/user-data/Patches/` |

### Pi

| What | Path |
|------|------|
| Code repo | `$HOME/MPE-Module` (override: `MPE_MODULE_REPO`) |
| Personal repo | `$HOME/MPE-Personal` (override: `MPE_PERSONAL_REPO`) |
| Surge CLI binary | `$HOME/surge/build/surge_xt_products/surge-xt-cli` |
| Factory / 3rd-party | `$HOME/surge/resources/data/patches_*` (often symlinked to MPE-Personal) |
| Custom patches | `$HOME/Documents/Surge XT/Patches/` |
| Surge log | `$HOME/surge-cli.log` |

## Quick Reference

```bash
# One-time PC setup
cd MPE-Module && ./scripts/setup-windows-symlinks.sh

# Daily
cd ../MPE-Personal && git add assets/user-data/Patches/ && git commit -m "patches"
cd ../MPE-Module && ./scripts/deploy-patches.sh

# Pi path (re)configuration
./scripts/configure-pi-paths.sh
./scripts/setup-pi-symlinks.sh

# Weekly backup
./scripts/sync-from-device.sh
```

Happy patching!
