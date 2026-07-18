# Backup Guide

Complete guide to backing up and restoring your Pi-Surge-MPE device.

## Overview

Backup data lives in the private **[MPE-Personal](https://github.com/M-Ferda/MPE-Personal)** repo (`assets/`). Deploy/sync scripts live in **MPE-Module**. Clone both as siblings.

Everything needed to restore the device is committed to MPE-Personal:
- Surge XT CLI binary (24MB)
- All 3,192 patches (422MB)
- System configurations
- Scripts and documentation

**Total repo size:** ~450MB

---

## Initial Setup (One Time Only)

### Step 1: Pull All Assets from Device

Run the pull script to download everything from the Pi:

```bash
cd "c:/Users/mitch/GitHub/MPE-Module"
bash scripts/pull-all-from-device.sh
```

This will download:
- Surge binary (24MB) → `assets/binaries/`
- Factory patches (47MB, 639 patches) → `assets/patches/factory/`
- Third-party patches (375MB, 2,553 patches) → `assets/patches/third-party/`
- Active system configs → `assets/configs/active/`
- User data → `assets/user-data/`

**Time:** 5-10 minutes (depends on network speed)

### Step 2: Commit to Git

```bash
cd ../MPE-Personal
git status
git add assets/
git commit -m "Initial backup: binary + patches + configs"
git push
```

**Note:** First push will be slow (~450MB upload). This is normal and only happens once.

---

## Ongoing Backups (Weekly Recommended)

### Quick Sync

Use the sync script to pull only changed files (configs, user data):

```bash
bash scripts/sync-from-device.sh
```

This syncs:
- System service files (if modified)
- User preferences
- Custom patches (if any)

**Time:** 10-30 seconds

### Review and Commit

```bash
git status                    # See what changed
git diff                      # Review changes
git add -A
git commit -m "Weekly backup $(date +%Y-%m-%d)"
git push
```

**Time:** 3-5 seconds (git only uploads changes)

---

## Disaster Recovery

### Scenario: SD Card Failed

**Prerequisites:**
- Fresh SD card with Raspberry Pi OS Lite
- SSH configured and Pi on network
- SSH key (`~/.ssh/surge_pi_key`) available

### Step 1: Clone Repository

On your Windows machine:

```bash
git clone https://github.com/yourusername/MPE-Module.git
cd MPE-Module
```

**Time:** 2-3 minutes (downloading ~450MB)

### Step 2: Deploy to Pi

```bash
bash scripts/deploy-all.sh
```

This will:
1. Create directories on Pi
2. Deploy Surge binary
3. Deploy all patches
4. Deploy scripts
5. Deploy Python scripts
6. Deploy systemd services
7. Deploy udev rules
8. Start services

**Time:** 5-10 minutes

### Step 3: Verify

```bash
ssh surge.local 'systemctl status surge-xt-cli'
ssh surge.local 'tail -30 ~/surge-cli.log'
```

✅ Device restored! Play your MIDI controller to test.

---

## What Gets Backed Up

| Item | Frequency | Size | Location |
|------|-----------|------|----------|
| Surge binary | One-time | 24MB | `assets/binaries/` |
| Factory patches | One-time | 47MB | `assets/patches/factory/` |
| Third-party patches | One-time | 375MB | `assets/patches/third-party/` |
| System configs | Weekly | <1MB | `assets/configs/active/` |
| User preferences | Weekly | <1MB | `assets/user-data/` |
| Custom patches | As needed | Varies | `assets/user-data/custom-patches/` |

---

## Backup Schedule

### Recommended Schedule

| Frequency | Action | Command |
|-----------|--------|---------|
| **One time** | Initial backup | `bash scripts/pull-all-from-device.sh` |
| **Weekly** | Sync changes | `bash scripts/sync-from-device.sh` |
| **Before updates** | Full sync | `bash scripts/sync-from-device.sh` |
| **After major changes** | Immediate sync | `bash scripts/sync-from-device.sh` |

### Automated Backups (Optional)

You can set up Windows Task Scheduler to run `sync-from-device.sh` automatically:

1. Open Task Scheduler
2. Create Basic Task
3. Name: "Pi-Surge Backup"
4. Trigger: Weekly (Sunday, 2:00 AM)
5. Action: Start a program
   - Program: `C:\Program Files\Git\bin\bash.exe`
   - Arguments: `scripts/sync-from-device.sh`
   - Start in: `c:\Users\mitch\GitHub\MPE Module`
6. Finish

**Note:** Your computer must be on for scheduled backups to run.

---

## Cloud Backup (Recommended)

For extra protection, sync your local repo folder to cloud storage:

### Option 1: OneDrive

Move your repo to OneDrive:
```bash
mv "c:\Users\mitch\GitHub\MPE Module" "c:\Users\mitch\OneDrive\GitHub\MPE Module"
```

OneDrive will automatically sync the ~450MB to cloud.

### Option 2: Google Drive / Dropbox

Add your repo folder to Google Drive or Dropbox sync.

**Benefit:** If your computer fails, the backup is still safe in the cloud.

---

## Troubleshooting

### Pull Script Fails

**Error:** "Cannot connect to Pi"

```bash
# Try with IP address
export PI_HOST=192.168.1.203
bash scripts/pull-all-from-device.sh
```

**Error:** "Permission denied"

```bash
# Check SSH key
ls -la ~/.ssh/surge_pi_key
chmod 600 ~/.ssh/surge_pi_key
```

### Deploy Script Fails

**Error:** "No such file or directory"

Make sure you ran `pull-all-from-device.sh` first to populate `assets/` directory.

### Git Push is Slow

First push with 450MB of assets will be slow (5-10 minutes). Subsequent pushes are fast because git only sends changes.

### Out of Disk Space

The assets take ~450MB locally. Make sure you have at least 1GB free on your Windows machine.

---

## FAQ

### Q: Do I need to pull patches every time?

**A:** No! After the initial `pull-all-from-device.sh`, use `sync-from-device.sh` which only pulls config changes and user data (not the large patch files).

### Q: Can I delete assets/ locally to save space?

**A:** Not recommended. If you delete assets/, you can't deploy to a fresh Pi without re-pulling from the device (which defeats the backup purpose).

### Q: What if the binary updates?

**A:** After updating Surge on the Pi, run `pull-all-from-device.sh` again to get the new binary, then commit and push.

### Q: Can I use this on multiple Pis?

**A:** Yes! The `deploy-all.sh` script can deploy to any Pi:

```bash
export PI_HOST=other-pi.local
bash scripts/deploy-all.sh
```

### Q: What's NOT backed up?

**Not in git:**
- SSH private keys (security risk)
- Temporary logs
- Build artifacts

These should be backed up separately or can be regenerated.

---

## Summary

**Initial setup:**
```bash
bash scripts/pull-all-from-device.sh  # One time
git add -A && git commit -m "Initial backup" && git push
```

**Weekly backups:**
```bash
bash scripts/sync-from-device.sh
git add -A && git commit -m "Backup $(date +%Y-%m-%d)" && git push
```

**Disaster recovery:**
```bash
git clone https://github.com/yourusername/MPE-Module.git
cd MPE-Module
bash scripts/deploy-all.sh
```

✅ Simple, complete, and reliable!
