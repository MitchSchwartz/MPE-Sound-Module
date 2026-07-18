# Surge XT Patch Editing Workflow

Complete guide for editing Surge XT patches on Windows and deploying them to your Raspberry Pi device.

## Two repos

| Repo | Role |
|------|------|
| **MPE-Module** | Code, docs, deploy scripts (this repo) |
| **MPE-Personal** | Private backup: `assets/user-data/Patches/`, factory/third-party copy, binary |

Clone both as siblings (`~/GitHub/MPE-Module` + `~/GitHub/MPE-Personal`). Scripts resolve `../MPE-Personal` automatically, or set `MPE_PERSONAL_REPO`.

## Overview

This workflow enables seamless patch editing:
1. Edit patches in Surge XT on Windows using the native GUI
2. Changes land in **MPE-Personal** via symlinks → commit there
3. Deploy from **MPE-Module** scripts (~5–10 seconds)
4. Test patches on your Roli Seaboard MIDI controller

## One-Time Setup

### Step 1: Create Windows Symlinks

Run once (from MPE-Module; requires MPE-Personal cloned beside it):

```bash
cd "c:/Users/mitch/GitHub/MPE-Module"
./scripts/setup-windows-symlinks.sh
```

This creates a junction from:
- `c:\Users\mitch\Documents\Surge XT\Patches` → `c:\Users\mitch\GitHub\MPE-Personal\assets\user-data\Patches`

Now any patches you save in Surge XT are automatically in MPE-Personal!

### Step 2: Verify Setup

```bash
# Check the junction was created
dir "c:\Users\mitch\Documents\Surge XT\Patches"
# Should show: <JUNCTION> pointing to git repo
```

Open Surge XT on Windows:
1. Launch Surge XT
2. Click the "Patch Browser" button
3. You should see:
   - `Mitch/` folder with your custom patches
   - `MIDI Programs/` folder
   - Your `Church - Mod.fxp` patch should be visible

## Daily Editing Workflow

### 1. Edit Patches in Windows

1. Launch Surge XT on Windows
2. Load an existing patch or start from Init
3. Modify the patch:
   - Adjust oscillators (wavetables, filters)
   - Add/modify effects (reverb, delay, chorus)
   - Set up modulation routing
   - Configure MPE settings
4. Save the patch:
   - Click "Store" or File → Save Patch
   - Choose location: `Mitch/YourPatchName.fxp`
   - Click Save

The patch is now automatically in your git repo!

### 2. Review and Commit Changes

Commit in **MPE-Personal** (not MPE-Module):

```bash
cd "c:/Users/mitch/GitHub/MPE-Personal"

git status
git diff assets/user-data/Patches/
git add assets/user-data/Patches/
git commit -m "Update Church - Mod.fxp: added chorus effect, adjusted reverb decay"
git push
```

### 3. Deploy to Pi

**Option A — direct deploy (default):**

```bash
./scripts/deploy-patches.sh
```

**Option B — if Pi uses `setup-pi-symlinks.sh`:** commit/push MPE-Personal, then on the Pi:

```bash
cd ~/MPE-Personal && git pull && sudo systemctl restart surge-xt-cli patch-browser
```

This script (Option A):
- Compresses your custom patches (~50KB)
- Uploads to Pi via SSH
- Extracts to `/home/mitch/Documents/Surge XT/Patches`
- Restarts the Surge CLI service
- Takes ~5-10 seconds total

### 4. Test on Pi

1. Pick up your Roli Seaboard
2. Play notes - Surge CLI is listening via MPE
3. Use the patch browser (OSC-controlled) to load patches
4. Your updated patch should load with the new changes

## Advanced Operations

### Browse Factory Patches for Reference

Factory and third-party patches (backup copy) live in MPE-Personal:
- Factory: `c:/Users/mitch/GitHub/MPE-Personal/assets/patches/patches_factory`
- Third-party: `c:/Users/mitch/GitHub/MPE-Personal/assets/patches/third-party/patches_3rdparty`

To use a factory patch as a starting point:
1. Navigate to the factory patches folder
2. Copy a patch to `assets/user-data/Patches/Mitch/`
3. Rename it (e.g., `Church.fxp` → `Church - Mod.fxp`)
4. Edit in Surge XT
5. Save (overwrites your copy, not the factory original)

### Sync Changes from Pi to Windows

If you want to back up the current state of the Pi (or if patches were somehow modified on the Pi):

```bash
./scripts/sync-from-device.sh
```

This pulls:
- User preferences (`SurgeXTUserDefaults.xml`)
- Custom patches from Pi → `assets/user-data/Patches`
- System configs and service files

Review and commit in MPE-Personal:
```bash
cd ../MPE-Personal
git status
git add -A
git commit -m "Sync from device $(date +%Y-%m-%d)"
git push
```

### Full System Deployment (Disaster Recovery)

If you need to completely restore the Pi from scratch:

```bash
./scripts/deploy-all.sh
```

This deploys:
- Surge XT binary
- Factory patches (639 patches, 47MB)
- Third-party patches (2,553 patches, 375MB)
- Custom patches
- System configs and services

Takes ~3-6 minutes. Only use this for:
- Setting up a new Pi
- Recovering from system failure
- Major system updates

For daily patch editing, use `deploy-patches.sh` instead (much faster).

## Troubleshooting

### Symlink Not Working

**Symptom**: Patches saved in Surge XT don't appear in git

**Solution**:
```bash
# Check if junction exists
dir "c:\Users\mitch\Documents\Surge XT\Patches"

# Should show: <JUNCTION> tag
# If not, recreate it:
cd "c:/Users/mitch/GitHub/MPE-Module"
./scripts/setup-windows-symlinks.sh
```

### Patches Not Appearing on Pi

**Symptom**: Deployed patches don't show up on the Pi

**Diagnosis**:
```bash
# Check if patches were deployed
ssh surge.local "ls -la '/home/mitch/Documents/Surge XT/Patches/Mitch/'"

# Check Surge CLI logs
ssh surge.local "tail -50 ~/surge-cli.log"

# Check service status
ssh surge.local "systemctl status surge-xt-cli"
```

**Solution**:
```bash
# Redeploy patches
./scripts/deploy-patches.sh

# If service failed to restart, do it manually
ssh surge.local "sudo systemctl restart surge-xt-cli"
```

### Deploy Script Connection Failure

**Symptom**: `Cannot connect to Pi`

**Solutions**:

1. Check Pi is powered on and connected to network
2. Test SSH connection manually:
   ```bash
   ssh -i ~/.ssh/surge_pi_key mitch@surge.local
   ```
3. Try using IP address instead of hostname:
   ```bash
   export PI_HOST=192.168.1.203
   ./scripts/deploy-patches.sh
   ```
4. Check SSH key permissions:
   ```bash
   chmod 600 ~/.ssh/surge_pi_key
   ```

### Surge XT Won't Open Patches Folder

**Symptom**: Clicking patch browser shows empty or wrong folder

**Solution**:
1. Restart Surge XT completely
2. Check symlink is working (see above)
3. Manually navigate to patches in file browser:
   - Open `c:\Users\mitch\Documents\Surge XT\Patches`
   - You should see `Mitch/` folder
   - If not, recreate symlink

### Git Shows Binary Diff as "Changed"

**Symptom**: `git diff` shows patch as changed but no readable diff

**This is normal**: `.fxp` files are binary. Git tracks that the file changed, but can't show a text diff.

To see what changed:
1. Load the patch in Surge XT (before committing)
2. Compare to previous version manually
3. Write a descriptive commit message explaining what you changed

## File Locations Reference

### Windows
- **Surge XT Install**: `c:\Users\mitch\Documents\Surge XT\`
- **Code repo**: `c:\Users\mitch\GitHub\MPE-Module\`
- **Personal repo**: `c:\Users\mitch\GitHub\MPE-Personal\`
- **Custom Patches** (via symlink): `c:\Users\mitch\Documents\Surge XT\Patches\Mitch\`
- **Factory Patches** (read-only backup): `c:\Users\mitch\GitHub\MPE-Personal\assets\patches\patches_factory\`
- **Third-party Patches** (read-only backup): `c:\Users\mitch\GitHub\MPE-Personal\assets\patches\third-party\patches_3rdparty\`

### Pi (surge.local)
- **Surge Binary**: `/home/mitch/surge/build/surge_xt_products/surge-xt-cli`
- **Factory Patches**: `/home/mitch/surge/resources/data/patches_factory/`
- **Third-party Patches**: `/home/mitch/surge/resources/data/patches_3rdparty/`
- **Custom Patches**: `/home/mitch/Documents/Surge XT/Patches/`
- **User Preferences**: `/home/mitch/.local/share/Surge XT/SurgeXTUserDefaults.xml`
- **Surge CLI Log**: `/home/mitch/surge-cli.log`

### Repo layout
```
MPE-Module/                    # code + deploy scripts
├── scripts/
│   ├── setup-windows-symlinks.sh
│   ├── setup-pi-symlinks.sh
│   ├── deploy-patches.sh
│   ├── deploy-all.sh
│   └── sync-from-device.sh
└── docs/PATCH-EDITING-WORKFLOW.md

MPE-Personal/                  # private backup (sibling clone)
└── assets/
    ├── binaries/surge-xt-cli
    ├── patches/patches_factory/
    ├── patches/third-party/patches_3rdparty/
    ├── configs/active/
    └── user-data/Patches/     # YOUR CUSTOM PATCHES (edit via symlink)
        └── Mitch/
```

## Quick Reference

### Common Commands

```bash
# One-time setup (from MPE-Module)
./scripts/setup-windows-symlinks.sh

# Daily workflow
cd ../MPE-Personal && git status
git add assets/user-data/Patches/ && git commit -m "Updated patches"
cd ../MPE-Module && ./scripts/deploy-patches.sh

# Weekly backup
./scripts/sync-from-device.sh
cd ../MPE-Personal && git commit -am "Backup from device" && git push

# Disaster recovery
./scripts/deploy-all.sh                 # Full system deploy (3-6 min)

# Diagnostics
ssh surge.local "tail -50 ~/surge-cli.log"              # View logs
ssh surge.local "systemctl status surge-xt-cli"         # Check service
ssh surge.local "ls -la '/home/mitch/Documents/Surge XT/Patches/Mitch/'"  # List patches
```

## Tips for Effective Patch Development

1. **Use descriptive names**: `Church - Mod.fxp`, not `Patch1.fxp`
2. **Commit often**: After each significant change
3. **Write good commit messages**: Explain what changed and why
4. **Test on Pi before pushing**: Make sure it sounds right on the actual hardware
5. **Keep backups**: Use `sync-from-device.sh` weekly for redundancy
6. **Organize in folders**: Use `Mitch/` for your patches, create subfolders if needed

## Example Workflow Session

```bash
# Morning: edit in Surge XT (saves into MPE-Personal via symlink)

cd "c:/Users/mitch/GitHub/MPE-Personal"
git add assets/user-data/Patches/Mitch/Church\ -\ Mod.fxp
git commit -m "Church - Mod: add chorus, longer reverb, brighter filter"
git push

cd "../MPE-Module"
./scripts/deploy-patches.sh

# Later: new patch → commit in MPE-Personal, deploy from MPE-Module

# End of day
./scripts/sync-from-device.sh
cd ../MPE-Personal && git commit -am "Daily backup $(date +%Y-%m-%d)" && git push
```

Happy patching!
