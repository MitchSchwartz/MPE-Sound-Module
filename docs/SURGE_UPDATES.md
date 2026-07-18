# Surge XT Updates & Stability Guide

## Detecting Nightly Build Issues

### Signs of Instability

**Critical (stop using, switch to stable):**
- ❌ Crashes during playback
- ❌ Corrupted audio output (crackling unrelated to CPU)
- ❌ Presets won't load/save
- ❌ MPE completely broken
- ❌ Can't launch at all

**Minor (report but can work around):**
- ⚠️ GUI glitches
- ⚠️ Specific preset crashes
- ⚠️ Feature X doesn't work (but Y does)
- ⚠️ Higher CPU than expected

**Normal (not bugs):**
- ✅ Different sound than older version (intentional improvements)
- ✅ New features present
- ✅ GUI layout changes

### How to Test for Stability

**Quick Smoke Test (5 minutes):**
```bash
# 1. Launch Surge
Surge-XT

# 2. Load a preset
# File > Load Preset > Pads > Any preset

# 3. Play MPE controller
# - Check all axes work (pitch, pressure, timbre)
# - Play for 30 seconds continuously

# 4. Switch presets rapidly
# - Load 5 different presets quickly
# - Check no crashes

# 5. Check CPU
top
# Surge should use < 80% of one core at idle
```

**Full Validation (30 minutes):**
```bash
# 1. Test 10+ presets from different categories
# 2. Test all MPE axes extensively
# 3. Test effects (reverb, delay, etc.)
# 4. Save a custom preset
# 5. Restart Surge and reload custom preset
# 6. Play for 15 minutes continuously
# 7. Monitor for crashes, glitches, CPU spikes
```

## Config Files & Preservation

### What Gets Saved Where

Surge XT stores settings in:

```
~/.config/surge-xt/
├── SurgeXT.conf          # Main settings (MPE, MIDI, audio)
├── surge-xt.midimap      # MIDI learn mappings
└── (other state files)

~/.local/share/surge-xt/
├── presets/              # User presets (YOUR CUSTOM PATCHES)
├── wavetables/           # User wavetables
├── patches/              # Legacy preset format
└── skins/                # Custom UI skins
```

### What's Safe to Update

**Always preserved across updates:**
- ✅ User presets (`~/.local/share/surge-xt/presets/`)
- ✅ User wavetables (`~/.local/share/surge-xt/wavetables/`)
- ✅ Most settings in `SurgeXT.conf`

**May change/reset:**
- ⚠️ MIDI mappings (if format changes)
- ⚠️ Some GUI preferences
- ⚠️ Experimental settings

**Never affected:**
- ✅ Your encoder controller script
- ✅ JACK configuration
- ✅ System settings

## Backup Before Updating

### Essential Backup (Always do this)

```bash
# Backup user data
cd ~
tar czf surge-backup-$(date +%Y%m%d).tar.gz \
  .config/surge-xt \
  .local/share/surge-xt

# Keep backups organized
mkdir -p ~/surge-backups
mv surge-backup-*.tar.gz ~/surge-backups/
```

### Restore if Needed

```bash
# If update breaks something
cd ~
tar xzf ~/surge-backups/surge-backup-YYYYMMDD.tar.gz

# Restart Surge
systemctl --user restart surge.service
```

## Update Workflow

### Safe Update Process

```bash
# 1. BACKUP FIRST
cd ~
tar czf surge-backup-$(date +%Y%m%d).tar.gz \
  .config/surge-xt \
  .local/share/surge-xt

# 2. Stop Surge
systemctl --user stop surge.service

# 3. Backup current binary
sudo cp /usr/local/bin/Surge-XT /usr/local/bin/Surge-XT.old

# 4. Build new version
cd ~/surge
git pull origin main
cd build
make clean
cmake .. (same flags as before)
make -j$(nproc)
sudo make install

# 5. Test manually (don't auto-start yet)
Surge-XT

# 6. Quick smoke test
# - Load preset
# - Play MPE
# - Check it works

# 7. If good, restart service
systemctl --user start surge.service

# 8. If bad, rollback
# sudo cp /usr/local/bin/Surge-XT.old /usr/local/bin/Surge-XT
# systemctl --user start surge.service
```

### Rollback Procedure

If an update breaks something:

```bash
# Stop broken version
systemctl --user stop surge.service

# Restore old binary
sudo cp /usr/local/bin/Surge-XT.old /usr/local/bin/Surge-XT

# Restore config (if needed)
cd ~
tar xzf ~/surge-backups/surge-backup-YYYYMMDD.tar.gz

# Restart
systemctl --user start surge.service

# Verify it works
Surge-XT
```

## Update Frequency Recommendations

### For Development/Testing Phase (Now - Milestone 1-3)

**Check for updates:** Weekly
**Update if:**
- Bug fixes relevant to your issues
- MPE improvements
- Performance improvements

**Reason:** You're testing anyway, might as well use latest code

### For Stable Performance Phase (Milestone 4+)

**Check for updates:** Monthly
**Update if:**
- Major bug fixes
- Security issues
- Features you want

**Don't update if:**
- It's working fine
- You have a gig soon
- No compelling reason

### For Live Production Use (Post-v1.0)

**Check for updates:** When official releases happen
**Update if:**
- You're between performances
- You can test thoroughly
- Benefits outweigh risks

**Don't update:**
- Week before a gig
- If current version is stable
- During performance season

## Monitoring for Issues

### Create a Test Log

```bash
# Create test log
cat > ~/surge-test-log.txt << EOF
Surge XT Testing Log
====================

Date: $(date)
Version: $(git -C ~/surge rev-parse --short HEAD)
Binary: $(md5sum /usr/local/bin/Surge-XT)

Test Results:
- Launch: [ ] Pass / [ ] Fail
- Load Preset: [ ] Pass / [ ] Fail
- MPE Pitch: [ ] Pass / [ ] Fail
- MPE Pressure: [ ] Pass / [ ] Fail
- MPE Timbre: [ ] Pass / [ ] Fail
- Preset Switch: [ ] Pass / [ ] Fail
- Save Preset: [ ] Pass / [ ] Fail
- 15min Playback: [ ] Pass / [ ] Fail

CPU Usage: ____%
Temperature: ____°C
Xruns: ____

Notes:
EOF

# Edit and fill in after testing
nano ~/surge-test-log.txt
```

### Automated Health Check Script

```bash
# Create health check script
cat > ~/pisurge/surge-health-check.sh << 'EOF'
#!/bin/bash
echo "=== Surge XT Health Check ==="
echo "Date: $(date)"
echo ""

# Check if Surge is running
if pgrep -x "Surge-XT" > /dev/null; then
    echo "✓ Surge-XT is running"

    # Check CPU usage
    CPU=$(ps aux | grep Surge-XT | grep -v grep | awk '{print $3}')
    echo "  CPU: $CPU%"

    # Check memory
    MEM=$(ps aux | grep Surge-XT | grep -v grep | awk '{print $4}')
    echo "  Memory: $MEM%"
else
    echo "✗ Surge-XT is NOT running"
fi

# Check JACK connections
echo ""
echo "JACK Connections:"
jack_lsp -c | grep Surge

# Check system temp
echo ""
echo "CPU Temperature: $(vcgencmd measure_temp)"

# Check for crashes in logs
echo ""
echo "Recent errors (last 10):"
journalctl --user -u surge.service --since "1 hour ago" | grep -i "error\|crash\|segfault" | tail -10
EOF

chmod +x ~/pisurge/surge-health-check.sh

# Run it
~/pisurge/surge-health-check.sh
```

## Version Tracking

### Know What You're Running

```bash
# Check Surge version
cat > ~/pisurge/surge-version.sh << 'EOF'
#!/bin/bash
echo "=== Surge XT Version Info ==="

# Git commit (if built from source)
if [ -d ~/surge/.git ]; then
    echo "Git commit: $(git -C ~/surge rev-parse --short HEAD)"
    echo "Git date: $(git -C ~/surge log -1 --format=%cd)"
    echo "Git branch: $(git -C ~/surge branch --show-current)"
fi

# Binary info
echo ""
echo "Binary: $(which Surge-XT)"
echo "Size: $(ls -lh /usr/local/bin/Surge-XT | awk '{print $5}')"
echo "Modified: $(stat -c %y /usr/local/bin/Surge-XT)"

# Binary hash (for verification)
echo ""
echo "MD5: $(md5sum /usr/local/bin/Surge-XT | awk '{print $1}')"
EOF

chmod +x ~/pisurge/surge-version.sh
~/pisurge/surge-version.sh
```

## Comparing Nightly vs Stable

### Side-by-Side Testing

```bash
# Keep both versions
sudo mv /usr/local/bin/Surge-XT /usr/local/bin/Surge-XT-nightly
sudo cp /usr/local/bin/Surge-XT.old /usr/local/bin/Surge-XT-stable

# Test stable
/usr/local/bin/Surge-XT-stable
# Play, take notes

# Test nightly
/usr/local/bin/Surge-XT-nightly
# Play, compare

# Choose winner
sudo cp /usr/local/bin/Surge-XT-(stable|nightly) /usr/local/bin/Surge-XT
```

## When to Switch to Stable Release

**Switch from nightly to stable if:**
- You encounter frequent crashes
- Critical features broken
- You need reliability for a performance
- Update causes regressions

**How to switch:**
```bash
cd ~/surge
git checkout release_xt/1.3.4  # Latest stable
cd build
make clean
cmake .. (same flags)
make -j$(nproc)
sudo make install
```

## Community Resources

### Report Issues

If you find a bug in nightly:

1. **Check if known:** [Surge GitHub Issues](https://github.com/surge-synthesizer/surge/issues)
2. **Report it:**
   - Describe the issue
   - Include git commit hash
   - Steps to reproduce
   - Your platform (Pi 4/5, OS version)
3. **Workaround:** Switch to stable until fixed

### Stay Informed

- [Surge Discord](https://discord.gg/spGANHw) - Active community, devs respond
- [Nightly Changelog](https://surge-synthesizer.github.io/nightlychangelog/) - See what's changing
- [GitHub Releases](https://github.com/surge-synthesizer/surge/releases) - Stable releases

## Best Practices

### Development Phase (Now)
- ✅ Use nightly/main branch
- ✅ Backup before each update
- ✅ Test immediately after update
- ✅ Keep old binary as backup
- ✅ Report bugs

### Production Phase (Live Gigs)
- ✅ Use stable release
- ✅ Update only between gigs
- ✅ Test thoroughly before performance
- ✅ Keep working version backed up
- ✅ Don't update week before gig

## Summary

**Config Preservation:**
- User presets: Always safe ✅
- Settings: Usually safe ✅
- MIDI maps: Backup recommended ⚠️

**Update Safety:**
- Backup first: Always ✅
- Test after update: Always ✅
- Keep old binary: Always ✅
- Can rollback: Always ✅

**Stability Detection:**
- Run smoke test after each update
- Monitor for crashes/glitches
- Check logs for errors
- Compare to known-good version

**You're safe to experiment with nightlies!** Just backup first. 🎹
