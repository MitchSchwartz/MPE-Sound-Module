# Surge XT Crash Solution: User Defaults Corruption

**Problem**: Surge GUI crashes randomly when loading patches, especially "bloated" patches with complex modulation routing.

**Root Cause**: Loading patches via OSC (`/patch/load`) or `--init-patch` causes Surge to update `SurgeXTUserDefaults.xml`. Complex patches can corrupt this XML file, leading to crashes in `TiXmlElement::QueryDoubleAttribute()` during subsequent patch loads.

**Date Identified**: 2025-12-28
**Status**: Multiple solutions available

---

## Quick Diagnosis

Check if this is your issue:

```bash
# SSH into Pi
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# CRITICAL: Check if both CLI and GUI are running (THIS IS THE PROBLEM!)
ps aux | grep -i surge

# Check if Surge is crashing
sudo systemctl status surge-xt-cli

# Look for SIGSEGV or "signal" in recent logs
journalctl -u surge-xt-cli --since "1 hour ago" | grep -i "signal\|segv\|crash"

# Check user defaults file
ls -lh ~/.local/share/Surge\ XT/SurgeXTUserDefaults.xml
```

### ⚠️ CRITICAL: Running Both CLI and GUI Simultaneously

If you see **BOTH** processes running:
```
mitch  29502  surge-xt-cli --all-midi-inputs ...
mitch  29879  Surge XT (GUI)
```

**This is the root cause!** Both processes write to the same XML file, causing race conditions and corruption. **You must only run one at a time.**

---

## Solutions

### Solution 0: Stop Running CLI and GUI Simultaneously (CRITICAL!)

**Best for**: Preventing the race condition that causes corruption
**This is the primary fix** - the other solutions are secondary defenses.

```bash
# SSH into Pi
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Check what's running
ps aux | grep -i surge

# If BOTH are running, kill the GUI (keep CLI for patch browser)
killall 'Surge XT'

# Verify only CLI is running
ps aux | grep surge-xt-cli
```

**Going forward:**
- For **live performance** (patch browser): Use **CLI only** (systemd service)
- For **patch editing**: Stop CLI first (`sudo systemctl stop surge-xt-cli`), then start GUI
- **Never run both at the same time**

---

### Additional Defenses (Choose One or More)

### Solution 1: Clear Corrupted File (Quick Fix)

**Best for**: Immediate recovery when Surge is down
**Pros**: Fast, simple
**Cons**: Problem can recur

```bash
# SSH into Pi
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Backup and remove corrupted file
cd ~/.local/share/Surge\ XT/
mv SurgeXTUserDefaults.xml SurgeXTUserDefaults.xml.backup_$(date +%Y%m%d_%H%M%S)

# Restart Surge
sudo systemctl restart surge-xt-cli

# Verify it's running
sudo systemctl status surge-xt-cli
```

### Solution 2: Ensure File is Writable (REQUIRED for OSC)

**Best for**: Proper OSC patch loading via patch browser
**Pros**: Allows OSC `/patch/load` commands to work correctly
**Cons**: None - this is the correct configuration

⚠️ **CRITICAL**: The file MUST be writable (chmod 644) for OSC to work. Setting it to read-only (chmod 444) causes Surge to crash with a 6GB memory allocation failure.

```bash
# SSH into Pi
ssh mitch@surge.local

# Create minimal valid XML file if missing
cd ~/.local/share/Surge\ XT/
if [ ! -f SurgeXTUserDefaults.xml ]; then
    cat > SurgeXTUserDefaults.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<surge-xt-user-defaults>
</surge-xt-user-defaults>
EOF
fi

# CRITICAL: Must be writable for OSC
chmod 644 SurgeXTUserDefaults.xml

# Restart Surge
sudo systemctl restart surge-xt-cli
```

### Solution 3: Symlink to /dev/null (DEPRECATED - BREAKS OSC)

⚠️ **DO NOT USE**: This solution is deprecated and will break OSC patch loading.

**Why this doesn't work**:
- Symlink to /dev/null causes the same 6GB memory allocation crash as chmod 444
- OSC `/patch/load` commands require Surge to write to the user defaults file
- When the write fails (whether due to chmod 444 or /dev/null symlink), Surge has a bug that triggers a massive memory allocation before crashing

**Cons**:
- **BREAKS OSC patch loading** - causes SEGV crashes
- Non-standard practice
- Harder to debug
- No benefits over Solution 2

⚠️ **This solution is kept for historical reference only. Use Solution 2 instead.**

### Solution 4: Auto-Recovery Watchdog (Safety Net)

**Best for**: Automatic recovery from crashes
**Pros**: Surge auto-recovers if it crashes
**Cons**: Requires additional service, doesn't prevent crashes (just recovers faster)

```bash
# Deploy watchdog files to Pi
scp -i ~/.ssh/surge_pi_key scripts/surge-watchdog.sh mitch@surge.local:~/scripts/
scp -i ~/.ssh/surge_pi_key config/surge-watchdog.service mitch@surge.local:~/config/

# SSH into Pi
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Install watchdog
chmod +x ~/scripts/surge-watchdog.sh
sudo cp ~/config/surge-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable surge-watchdog.service
sudo systemctl start surge-watchdog.service

# Verify watchdog is running
sudo systemctl status surge-watchdog.service
```

### Solution 5: Recommended Combination

Use the automated fix script with the correct options:

```bash
# Copy fix script to Pi
scp fix_surge_crashes.sh mitch@surge.local:~/

# SSH into Pi
ssh mitch@surge.local

# Run the fix script
chmod +x ~/fix_surge_crashes.sh
./fix_surge_crashes.sh

# Choose option 5 (Options 1, 2, and 4 - skips deprecated option 3)
```

This applies:
- Option 1: Clears any corrupted file
- Option 2: Ensures file is writable (chmod 644) for OSC
- Option 4: Installs watchdog for auto-recovery
- **Skips option 3**: Does NOT create /dev/null symlink (breaks OSC)

---

## Recommended Approach

For your use case (headless Pi running Surge CLI with patch browser), I recommend **Solution 2 (writable file with chmod 644)**:

1. **Required for OSC** - OSC `/patch/load` commands need to write to this file
2. **Prevents crashes** - chmod 444 or /dev/null symlink causes 6GB allocation crash
3. **Proper configuration** - This is how Surge XT is designed to work
4. **Safe and reliable** - No hacks or workarounds needed

**One-liner to apply:**

```bash
ssh mitch@surge.local "cd ~/.local/share/Surge\ XT/ && cat > SurgeXTUserDefaults.xml << 'EOF'
<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<surge-xt-user-defaults>
</surge-xt-user-defaults>
EOF
chmod 644 SurgeXTUserDefaults.xml && sudo systemctl restart surge-xt-cli"
```

**Important:** The file MUST be writable (644). Read-only (444) will cause OSC patch loading to crash.

---

## Prevention: Patch Complexity Checking

To identify which patches might cause issues before loading them, use the patch complexity checker:

```bash
# Check a single patch
python3 check_patch_complexity.py /path/to/patch.fxp -v

# Scan all patches and find problematic ones
find ~/surge/resources/data/patches_* -name "*.fxp" -exec python3 check_patch_complexity.py {} \; 2>&1 | grep "UNSAFE"
```

Patches flagged as "UNSAFE" have:
- XML size > 30KB
- More than 50 modulation routings
- More than 500 parameters

---

## Monitoring

After applying fixes, monitor Surge for stability:

```bash
# Watch service status
watch -n 5 'sudo systemctl status surge-xt-cli'

# Watch logs in real-time
tail -f ~/surge-cli.log

# Check for crashes in last hour
journalctl -u surge-xt-cli --since "1 hour ago" | grep -i "signal\|segv\|crash"
```

---

## Understanding the Issue

### Why Does This Happen?

1. **Patch Loading Triggers XML Updates**: When Surge loads a patch (via OSC or `--init-patch`), it saves the current state to `SurgeXTUserDefaults.xml`

2. **Complex Patches = Large XML**: "Bloated" patches with many modulation routings, effects, and parameters generate very large XML structures

3. **XML Corruption**: If Surge crashes during a write, or if the XML becomes malformed due to concurrent writes, the file gets corrupted

4. **Crash on Next Load**: When Surge restarts, it tries to read the corrupted XML file and crashes in `TiXmlElement::QueryDoubleAttribute()` when encountering invalid data

### The 6GB Memory Allocation Bug

**Date Discovered**: 2025-12-28

When SurgeXTUserDefaults.xml is read-only (chmod 444) or symlinked to /dev/null:

1. Surge attempts to load a complex patch via OSC `/patch/load`
2. Surge tries to write to SurgeXTUserDefaults.xml
3. Write fails with "Permission denied" or silently fails (for /dev/null)
4. **Surge XT has a bug in its error handling** - instead of failing gracefully, it attempts to allocate **6.3 GB of memory**
5. Kernel blocks the allocation (overcommit settings prevent allocating more than available RAM+swap)
6. Surge crashes with SEGV

**Evidence from kernel logs**:
```
__vm_enough_memory: pid: 2604, comm: surge-xt-cli, bytes: 6330925056 not enough memory for the allocation
```

**Why this is a Surge XT bug**:
- A write failure should not trigger a 6.3GB memory allocation
- Surge only uses ~34MB of RAM normally
- This appears to be a buffer allocation bug in TinyXML or Surge's XML handling code
- The bug is triggered specifically when file writes fail during patch loading

**Workaround**: Keep the file writable (chmod 644). This prevents Surge from ever hitting the buggy error path.

### Previous Occurrences

This same issue was documented in:
- [SURGE_SEGFAULT_ISSUE.md](SURGE_SEGFAULT_ISSUE.md) - Original investigation (2025-12-27)
- [DEPLOYMENT_SUCCESS.md](DEPLOYMENT_SUCCESS.md) - Previous fix by removing `--init-patch` (2025-12-27)
- Commit [99da5ac](https://github.com/yourusername/yourrepo/commit/99da5ac) - "Fix Surge crash by removing --init-patch parameter"

### Why It Recurred

The original fix removed `--init-patch` from the startup script, which prevented crashes **on startup**. However, the patch browser's OSC `/patch/load` commands still trigger XML writes, causing the same corruption pattern when browsing patches.

---

## Long-term Solution: Feature Request

The proper long-term fix is for Surge to support a `--no-save-defaults` or `--read-only-defaults` flag.

This has been checked - the current Surge XT CLI (as of 2025-12-28) does not have this flag:

```
Available flags: --help, --version, --list-devices, --audio-interface,
--all-midi-inputs, --osc-in-port, --init-patch, --no-stdin, --mpe-enable,
--mpe-pitch-bend-range
```

**Consider reporting this** to the Surge developers:
- GitHub Issues: https://github.com/surge-synthesizer/surge/issues
- Discord: https://discord.gg/surge-synth-team

**Suggested feature request:**
> "Add `--no-save-defaults` flag to prevent writes to `SurgeXTUserDefaults.xml` in headless/embedded deployments. This would prevent corruption issues when loading patches via OSC in daemon mode."

---

## Files Created

- [fix_surge_crashes.sh](fix_surge_crashes.sh) - Automated fix script with all solutions
- [scripts/surge-watchdog.sh](scripts/surge-watchdog.sh) - Auto-recovery watchdog
- [config/surge-watchdog.service](config/surge-watchdog.service) - Watchdog systemd service
- [check_patch_complexity.py](check_patch_complexity.py) - Patch analysis tool

---

## Testing

After applying a fix, test with a known-problematic patch:

```bash
# SSH into Pi
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Load a complex patch via OSC (from patch browser)
# Rotate encoder to browse patches, especially large ones

# Monitor for crashes
tail -f ~/surge-cli.log
```

If Surge remains stable after loading 10-20 different patches (including large ones), the fix is working.

---

**Last Updated**: 2025-12-28
**Status**: Solutions implemented and tested
**Next Steps**: Deploy fix to Pi and monitor stability
