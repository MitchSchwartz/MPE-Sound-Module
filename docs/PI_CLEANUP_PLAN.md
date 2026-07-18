# Raspberry Pi Filesystem Cleanup Plan

**Date**: 2025-12-28
**Goal**: Clean up Pi filesystem, migrate everything possible to git for backup and reproducibility

## Current State Analysis

### Three Filesystem Locations:
1. **Windows Git Repo**: `c:\Users\mitch\GitHub\MPE Module` - Clean, organized, version controlled
2. **Pi Home Directory**: `/home/mitch/` - **MESSY** - 27+ loose scripts, duplicates, old backups
3. **Pi MPE-Module Git**: `/home/mitch/MPE-Module/` - Up to date with Windows repo

### Major Issues:
- **Duplication**: Scripts exist in 3 places (home, scripts/, MPE-Module/)
- **Large Backups**: 245MB corrupted patch backup from Dec 27
- **Orphaned Files**: Old GUI/VNC scripts, deprecated MIDI scripts
- **Service Files**: systemd services in wrong location (home dir instead of /etc)

---

## File Categorization

### Category 1: KEEP in Git (Setup/Deployment Scripts)

**Purpose**: Enable fresh Pi setup from scratch

These should be moved into git repo under appropriate structure:

**Setup Scripts (one-time installation):**
- `setup-power-button.sh` → `archive/setup-tools/setup-power-button.sh`

**Install Scripts:**
- `install_patch_browser.sh` → `archive/setup-tools/install-patch-browser.sh`

**GUI/X11 Setup (for reference, even though not used):**
- `start-x11.sh`, `start-x-vnc.sh`, `launch-gui-vnc.sh`, `start-gui-with-vnc.sh`
  → `archive/setup-tools/gui-setup/` (documented as "not needed for headless CLI")

**Mode Switchers (deprecated but useful for reference):**
- `switch-to-gui.sh`, `switch-to-cli.sh`, `enable-gui.sh`, `disable-gui.sh`
  → Already in `archive/legacy-scripts/` on Windows

### Category 2: KEEP Active (Runtime Scripts)

**These need to stay accessible but should live in git:**

**Active Runtime:**
- `/home/mitch/start-surge-cli.sh` → Should be symlink to MPE-Module/scripts/start-surge-cli.sh
- `/home/mitch/patch_browser_ui.py` → Should be symlink to MPE-Module/patch_browser_ui.py
- `/home/mitch/boot_animation.py` → Should be symlink to MPE-Module/boot_animation.py
- `/home/mitch/start-patch-browser.sh` → Should be symlink to MPE-Module/scripts/start-patch-browser.sh

**Deployed Scripts (in /home/mitch/scripts/):**
- Already deployed from git, these are fine

### Category 3: BACKUP to Git Then DELETE

**User Data (needs backup):**
- `.patch_browser_favorites.json` → Copy to `assets/user-data/`
- `surge-cli.log` → Optional, can recreate
- `.gitconfig` → Copy to `assets/user-data/` if custom

**Patch Corruption Investigation Tools:**
- `add_mpe_timbre_modulation.py` → Move to `archive/development-tools/` (document as "caused corruption")
- `test_add_timbre_mod.py`, `extract_timbre_targets.py`, `find_filter_params.py`, `analyze_fxp.py`
  → Move to `archive/development-tools/patch-analysis/`

**Test Patches:**
- `Church_*.fxp` files → Move to `archive/development-tools/test-patches/`

### Category 4: DELETE (Pure Cruft)

**Large Backups:**
- `surge_patches_backup_20251227_182142/` - **245MB** - Corrupted patches, already replaced
- `/home/mitch/backups/` - Old script backups, superseded by git

**Backup Files:**
- `patch_browser_ui.py.backup.*` (3 files)
- `start-surge-cli.sh.backup`
- `.bash_profile.gui_backup`
- `.bash_profile.disabled`
- `.xinitrc.gui_backup`
- `.xsession-errors.old`

**Deprecated Scripts (already in git archive):**
- Old MIDI scripts: `enable-mpe-midi.sh`, `enable-mpe-midi.py`, `ensure-mpe-enabled.sh`, `auto-connect-midi.sh`
- Service file copies in home dir: `surge-xt-cli.service`, `patch-browser.service`, `boot-animation.service`
  (The real ones are in /etc/systemd/system/)

**Logs:**
- `vnc.log`, `surge-gui.log`, `mpe-enable.log`, `midi-connect.log` - old, can recreate

**Old Projects:**
- `pisurge/` - 11MB - old abandoned git repo

**Empty Directories:**
- Desktop, Downloads, Music, Pictures, Public, Templates, Videos (all 4KB, empty)

---

## Proposed New Structure

### On Pi:

```
/home/mitch/
├── MPE-Module/              # Git repo - source of truth
│   ├── scripts/             # Active runtime scripts
│   ├── patch_browser_ui.py
│   ├── boot_animation.py
│   ├── assets/
│   │   ├── user-data/       # User favorites, configs
│   │   └── patches/
│   └── archive/             # Setup/historical scripts
│       ├── setup-tools/
│       └── development-tools/
│
├── surge/                   # Surge XT build (not in git)
│
├── scripts/                 # Symlinks to MPE-Module/scripts/
│   ├── start-surge-cli.sh -> ../MPE-Module/scripts/start-surge-cli.sh
│   ├── surge-watchdog.sh -> ../MPE-Module/scripts/surge-watchdog.sh
│   └── ...
│
├── start-surge-cli.sh -> MPE-Module/scripts/start-surge-cli.sh
├── patch_browser_ui.py -> MPE-Module/patch_browser_ui.py
├── boot_animation.py -> MPE-Module/boot_animation.py
├── start-patch-browser.sh -> MPE-Module/scripts/start-patch-browser.sh
│
└── surge-cli.log           # Runtime log (not in git)
```

### In Git Repo:

```
MPE Module/
├── scripts/                        # Active production scripts
│   ├── start-surge-cli.sh
│   ├── surge-watchdog.sh
│   ├── start-patch-browser.sh
│   └── README.md
│
├── patch_browser_ui.py
├── boot_animation.py
│
├── assets/
│   ├── user-data/                  # Backed up user data
│   │   ├── patch_browser_favorites.json
│   │   └── gitconfig
│   └── patches/
│
└── archive/
    ├── setup-tools/                # One-time setup scripts
    │   ├── setup-power-button.sh
    │   ├── install-patch-browser.sh
    │   └── gui-setup/              # X11/VNC scripts (not needed)
    │
    ├── development-tools/          # Analysis/debugging tools
    │   ├── add_mpe_timbre_modulation.py  # ⚠️ Caused corruption!
    │   ├── patch-analysis/
    │   └── test-patches/
    │
    └── legacy-scripts/             # Superseded scripts
        └── mode-switchers/
```

---

## Required Service File Updates

After moving to symlinks, update these systemd services:

**No changes needed!** All service files reference `/home/mitch/...` which will be symlinks pointing to MPE-Module.

Example:
- Service: `ExecStart=/home/mitch/start-surge-cli.sh`
- File: `/home/mitch/start-surge-cli.sh` → (symlink) → `/home/mitch/MPE-Module/scripts/start-surge-cli.sh`
- Works transparently!

---

## Execution Plan

### Phase 1: Backup User Data to Git (Windows)
```bash
# Pull user data from Pi to git repo
scp -i ~/.ssh/surge_pi_key mitch@surge.local:.patch_browser_favorites.json assets/user-data/
scp -i ~/.ssh/surge_pi_key mitch@surge.local:.gitconfig assets/user-data/gitconfig

# Commit to git
git add assets/user-data/
git commit -m "Backup Pi user data before cleanup"
```

### Phase 2: Move Setup Scripts to Archive (Windows)
```bash
# Copy setup scripts from Pi to git archive
scp -i ~/.ssh/surge_pi_key mitch@surge.local:setup-power-button.sh archive/setup-tools/
scp -i ~/.ssh/surge_pi_key mitch@surge.local:install_patch_browser.sh archive/setup-tools/

# Copy development tools
mkdir -p archive/development-tools/patch-analysis
scp -i ~/.ssh/surge_pi_key mitch@surge.local:add_mpe_timbre_modulation.py archive/development-tools/
scp -i ~/.ssh/surge_pi_key mitch@surge.local:{test_add_timbre_mod.py,extract_timbre_targets.py,find_filter_params.py,analyze_fxp.py} archive/development-tools/patch-analysis/

# Copy test patches
mkdir -p archive/development-tools/test-patches
scp -i ~/.ssh/surge_pi_key mitch@surge.local:Church_*.fxp archive/development-tools/test-patches/

# Commit to git
git add archive/
git commit -m "Archive Pi setup and development tools"
git push
```

### Phase 3: Clean Up Pi Filesystem
```bash
# SSH to Pi
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Delete large backups (256MB total!)
rm -rf ~/surge_patches_backup_20251227_182142
rm -rf ~/backups

# Delete old project
rm -rf ~/pisurge

# Delete backup files
rm -f ~/*.backup*
rm -f ~/.bash_profile.gui_backup ~/.bash_profile.disabled
rm -f ~/.xinitrc.gui_backup
rm -f ~/.xsession-errors.old

# Delete old logs
rm -f ~/vnc.log ~/surge-gui.log ~/mpe-enable.log ~/midi-connect.log

# Delete deprecated scripts (now in git)
rm -f ~/setup-power-button.sh ~/install_patch_browser.sh
rm -f ~/add_mpe_timbre_modulation.py ~/test_add_timbre_mod.py
rm -f ~/extract_timbre_targets.py ~/find_filter_params.py ~/analyze_fxp.py
rm -f ~/Church_*.fxp

# Delete old MIDI scripts
rm -f ~/enable-mpe-midi.{sh,py} ~/ensure-mpe-enabled.sh ~/auto-connect-midi.sh

# Delete GUI/VNC scripts
rm -f ~/start-x11.sh ~/start-x-vnc.sh ~/launch-gui-vnc.sh ~/start-gui-with-vnc.sh
rm -f ~/enable-gui.sh ~/disable-gui.sh ~/switch-to-{gui,cli}.sh

# Delete service file copies (real ones in /etc/systemd/system/)
rm -f ~/surge-xt-cli.service ~/patch-browser.service ~/boot-animation.service

# Delete requirements.txt (if not needed)
rm -f ~/requirements.txt

# Delete empty directories
rmdir ~/Desktop ~/Downloads ~/Music ~/Pictures ~/Public ~/Templates ~/Videos 2>/dev/null || true
```

### Phase 4: Create Symlinks for Active Files
```bash
# Still on Pi

# Remove old loose files (if they exist as files, not symlinks)
rm -f ~/start-surge-cli.sh ~/patch_browser_ui.py ~/boot_animation.py ~/start-patch-browser.sh

# Create symlinks to MPE-Module
ln -s /home/mitch/MPE-Module/scripts/start-surge-cli.sh ~/start-surge-cli.sh
ln -s /home/mitch/MPE-Module/patch_browser_ui.py ~/patch_browser_ui.py
ln -s /home/mitch/MPE-Module/boot_animation.py ~/boot_animation.py
ln -s /home/mitch/MPE-Module/scripts/start-patch-browser.sh ~/start-patch-browser.sh

# Verify symlinks
ls -lah ~/ | grep '\->'
```

### Phase 5: Update ~/scripts/ Directory
```bash
# Still on Pi

# scripts/ directory should reference MPE-Module/scripts/
cd ~/scripts
rm -f *.sh  # Remove all old scripts

# Create symlinks to MPE-Module
ln -s ../MPE-Module/scripts/start-surge-cli.sh .
ln -s ../MPE-Module/scripts/surge-watchdog.sh .
ln -s ../MPE-Module/scripts/detect-audio-device.sh .
ln -s ../MPE-Module/scripts/test-audio-detection.sh .
ln -s ../MPE-Module/scripts/check-surge-mode.sh .
ln -s ../MPE-Module/scripts/start-patch-browser.sh .

# Verify
ls -lah ~/scripts/
```

### Phase 6: Test Services
```bash
# Still on Pi

# Restart services to ensure symlinks work
sudo systemctl restart surge-xt-cli
sudo systemctl restart patch-browser
sudo systemctl restart boot-animation

# Check status
sudo systemctl status surge-xt-cli
sudo systemctl status patch-browser
sudo systemctl status boot-animation

# Check if Surge is running
ps aux | grep surge-xt-cli
```

### Phase 7: Final Verification
```bash
# On Windows

# Pull updated MPE-Module from Pi (should be clean now)
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "cd ~/MPE-Module && git status"

# Verify filesystem is clean
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "ls -lah ~/ | grep -v '^d' | wc -l"
# Should be < 10 files (just dotfiles and symlinks)

# Check disk space savings
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "df -h ~"
```

---

## Expected Results

**Before Cleanup:**
- 27+ loose scripts in `/home/mitch/`
- 245MB+ in old backups
- Duplicated files across 3 locations
- No clear source of truth

**After Cleanup:**
- **Source of truth**: `/home/mitch/MPE-Module/` (git repo)
- **Active files**: Symlinks from home directory to git repo
- **Backed up in git**: All setup scripts, user data, configs
- **Disk savings**: ~256MB freed
- **Clarity**: One location for all code

---

## Future Device Setup

With this structure, setting up a new Pi is simple:

```bash
# 1. Clone repo
git clone https://github.com/yourusername/MPE-Module.git ~/MPE-Module

# 2. Run setup scripts from archive
bash ~/MPE-Module/archive/setup-tools/install-dependencies.sh
bash ~/MPE-Module/archive/setup-tools/setup-power-button.sh

# 3. Deploy active scripts
bash ~/MPE-Module/scripts/deploy-all.sh

# 4. Create symlinks
ln -s ~/MPE-Module/scripts/start-surge-cli.sh ~/start-surge-cli.sh
ln -s ~/MPE-Module/patch_browser_ui.py ~/patch_browser_ui.py
# ... etc

# 5. Enable services
sudo systemctl enable surge-xt-cli patch-browser boot-animation
sudo systemctl start surge-xt-cli
```

All config, scripts, and setup instructions are version controlled and backed up!

---

**Status**: Ready to execute
**Risk**: Low - Everything backed up to git first, services use symlinks (transparent)
**Rollback**: Git repo unchanged until Phase 2 complete, can re-deploy old files if needed
