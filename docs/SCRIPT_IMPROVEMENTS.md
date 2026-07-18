# Surge Mode Switching Script Improvements

**Date**: 2025-12-28
**Issue**: Running CLI and GUI simultaneously causes XML corruption crashes
**Solution**: Improved scripts that ensure only one runs at a time

---

## Summary of Changes

### Problems with Old Scripts

1. **`switch-to-gui.sh`** - Doesn't actually start the GUI, just prints instructions
2. **`enable-gui.sh`/`disable-gui.sh`** - Require unnecessary reboots
3. **No verification** - Scripts don't check if both processes are running
4. **Easy to forget** - Users can accidentally start both

### New Improved Scripts

✅ **[scripts/switch-to-cli-improved.sh](scripts/switch-to-cli-improved.sh)**
- Stops GUI completely (force kill if needed)
- Verifies GUI stopped before starting CLI
- Shows running processes to confirm
- **No reboot required**

✅ **[scripts/switch-to-gui-improved.sh](scripts/switch-to-gui-improved.sh)**
- Stops CLI service
- Actually starts the GUI (old script didn't!)
- Verifies only one is running
- Auto-restarts CLI if GUI fails to start
- **No reboot required**

✅ **[scripts/check-surge-mode.sh](scripts/check-surge-mode.sh)**
- **NEW** - Diagnostic tool
- Shows which mode is currently running
- **Detects if both are running** (critical error)
- Provides fix commands if there's a problem

---

## Quick Reference

### Check Current Mode
```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
```

### Switch to CLI (Live Performance)
```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/switch-to-cli-improved.sh"
```

### Switch to GUI (Patch Editing)
```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/switch-to-gui-improved.sh"
# Then connect via VNC to edit patches
```

---

## Deployment Instructions

### 1. Copy New Scripts to Pi

```bash
# Make scripts executable locally
chmod +x scripts/switch-to-cli-improved.sh
chmod +x scripts/switch-to-gui-improved.sh
chmod +x scripts/check-surge-mode.sh

# Copy to Pi
scp -i ~/.ssh/surge_pi_key scripts/switch-to-cli-improved.sh mitch@surge.local:~/scripts/
scp -i ~/.ssh/surge_pi_key scripts/switch-to-gui-improved.sh mitch@surge.local:~/scripts/
scp -i ~/.ssh/surge_pi_key scripts/check-surge-mode.sh mitch@surge.local:~/scripts/

# Make executable on Pi
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "chmod +x ~/scripts/*.sh"
```

### 2. Fix Current State (Both Running)

```bash
# Check if both are running (THIS IS THE PROBLEM)
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"

# If both running, kill GUI and keep CLI for patch browser
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "pkill -f 'Surge XT'"

# Verify only CLI is running
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
```

### 3. Optional: Apply Read-Only Protection

Even with proper scripts, add read-only protection as defense-in-depth:

```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "cd ~/.local/share/Surge\ XT/ && rm -f SurgeXTUserDefaults.xml && touch SurgeXTUserDefaults.xml && chmod 444 SurgeXTUserDefaults.xml && sudo systemctl restart surge-xt-cli"
```

---

## Old Scripts - What to Do With Them

### Keep (Still Useful)
- [scripts/launch-gui-vnc.sh](scripts/launch-gui-vnc.sh) - Can still use to manually launch GUI

### Replace with Improved Versions
- [scripts/switch-to-cli.sh](scripts/switch-to-cli.sh) → Use `switch-to-cli-improved.sh` instead
- [scripts/switch-to-gui.sh](scripts/switch-to-gui.sh) → Use `switch-to-gui-improved.sh` instead

### Probably Don't Need
- [scripts/enable-gui.sh](scripts/enable-gui.sh) - Reboots to enable GUI auto-start on boot (overkill)
- [scripts/disable-gui.sh](scripts/disable-gui.sh) - Reboots to disable GUI auto-start (overkill)

**Why?** You want **CLI to auto-start on boot** (for live performance), and only switch to GUI temporarily when editing patches. The reboot scripts set up persistent GUI mode, which defeats the purpose of a headless performance system.

---

## Typical Workflows

### Daily Use (Live Performance)
```bash
# Pi boots → CLI auto-starts via systemd → Patch browser controls it
# No manual intervention needed
```

### Editing Patches
```bash
# 1. Switch to GUI mode
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/switch-to-gui-improved.sh"

# 2. Connect via VNC and edit patches
# VNC to: surge.local:5900

# 3. When done, switch back to CLI
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/switch-to-cli-improved.sh"
```

### Troubleshooting Crashes
```bash
# 1. Check what's running
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"

# 2. If both running (BAD!), kill GUI
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "pkill -f 'Surge XT'"

# 3. Verify only CLI running
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
```

---

## Why No Reboot Needed?

**Old approach (enable-gui.sh/disable-gui.sh):**
- Modifies `.bash_profile` and `.xinitrc` to auto-start GUI on login
- Requires reboot to apply changes
- Makes GUI mode persistent (not what you want for a performance system)

**New approach (switch-to-X-improved.sh):**
- Just stops one process and starts the other
- Works instantly (2-3 seconds)
- CLI remains the default boot mode (what you want)
- GUI is temporary for editing only

---

## Key Improvements

1. ✅ **Instant switching** - No reboot needed (2-3 seconds)
2. ✅ **Verification** - Scripts check only one is running
3. ✅ **Error recovery** - Auto-restart CLI if GUI fails
4. ✅ **Diagnostic tool** - `check-surge-mode.sh` detects problems
5. ✅ **Clear messages** - Scripts explain what they're doing
6. ✅ **Fail-safe** - Force kills if needed, verifies state

---

## Testing the Scripts

### Test 1: Switch from CLI to GUI
```bash
# Starting state: CLI running
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
# Should show: "Mode: CLI"

# Switch to GUI
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/switch-to-gui-improved.sh"
# Should show: "Successfully switched to GUI mode!"

# Verify
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
# Should show: "Mode: GUI"
```

### Test 2: Switch from GUI to CLI
```bash
# Starting state: GUI running
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
# Should show: "Mode: GUI"

# Switch to CLI
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/switch-to-cli-improved.sh"
# Should show: "Successfully switched to CLI mode!"

# Verify
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
# Should show: "Mode: CLI"
```

### Test 3: Detect Both Running (Error State)
```bash
# Manually create the bad state
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "sudo systemctl start surge-xt-cli && DISPLAY=:0 ~/surge/build/src/surge-xt/surge-xt_artefacts/Release/Standalone/Surge\ XT &"

# Check (should detect problem)
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "./scripts/check-surge-mode.sh"
# Should show: "CRITICAL: BOTH CLI AND GUI ARE RUNNING!"
```

---

**Last Updated**: 2025-12-28
**Status**: Improved scripts ready for deployment
**Next Steps**: Deploy to Pi and test
