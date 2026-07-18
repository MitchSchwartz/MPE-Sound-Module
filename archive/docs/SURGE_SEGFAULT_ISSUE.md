# CRITICAL: Surge XT CLI Segfault Issue

**Status**: 🔴 BLOCKING
**Discovered**: 2025-12-27
**Impact**: Surge CLI cannot launch at all

---

## Problem Summary

The Surge XT CLI binary at `/home/mitch/surge/build/surge_xt_products/surge-xt-cli` is crashing immediately with a segmentation fault (SIGSEGV, exit code 139) on every launch attempt, regardless of parameters.

## Evidence

### Test 1: Minimal launch (no parameters)
```bash
$ /home/mitch/surge/build/surge_xt_products/surge-xt-cli
Segmentation fault      (exit code: 139)
```

### Test 2: With MPE parameters (no audio)
```bash
$ surge-xt-cli --all-midi-inputs --mpe-enable --mpe-pitch-bend-range=48
Segmentation fault      (exit code: 139)
```

### Test 3: With full parameters
```bash
$ surge-xt-cli \
  --all-midi-inputs \
  --mpe-enable \
  --mpe-pitch-bend-range=48 \
  --init-patch="/home/mitch/surge/resources/data/patches_factory/Keys/Church.fxp" \
  --audio-interface=0.23
Segmentation fault      (exit code: 139)
```

### Systemd Logs
```
surge-xt-cli.service: Main process exited, code=killed, status=11/SEGV
surge-xt-cli.service: Failed with result 'signal'
```

## Investigation

### What We Ruled Out

- ❌ **Audio device issues**: Crashes even without `--audio-interface` parameter
- ❌ **Init patch issues**: Crashes even without `--init-patch` parameter
- ❌ **MIDI issues**: Crashes even without `--all-midi-inputs` parameter
- ❌ **Our audio detection script**: Old startup script crashes the same way
- ❌ **Systemd configuration**: Binary crashes when run manually too

### What We Know

- ✅ Binary exists and is executable
- ✅ `--list-devices` flag works (shows all audio devices)
- ✅ `--help` likely works (for listing options)
- ❌ Actual audio engine initialization crashes immediately

## System Information

**Pi Configuration**:
- OS: Raspberry Pi OS Lite 64-bit (Debian Trixie)
- Kernel: 6.12.47+rpt-rpi-v8
- Hardware: Raspberry Pi 5
- Surge Build: `/home/mitch/surge/build/surge_xt_products/surge-xt-cli`

**Audio Devices Present**:
```
card 0: vc4hdmi0 [vc4-hdmi-0]
card 1: Headphones [bcm2835 Headphones]
card 2: vc4hdmi1 [vc4-hdmi-1]
card 3: S3 [Sound Blaster Play! 3]
card 4: BLOCK [Seaboard BLOCK]
```

## Possible Causes

### 1. Library Dependency Issues
The binary may be missing required shared libraries or have version mismatches.

**Check**:
```bash
ldd /home/mitch/surge/build/surge_xt_products/surge-xt-cli
```

Look for:
- Missing libraries (`not found`)
- Wrong library versions
- ALSA/audio library issues

### 2. Incompatible Build
The Surge binary may have been compiled for a different:
- Architecture (wrong ARM variant?)
- OS version (Debian version mismatch?)
- Kernel version

**Verify build**:
```bash
file /home/mitch/surge/build/surge_xt_products/surge-xt-cli
```

Should show: `ELF 64-bit LSB executable, ARM aarch64`

### 3. Corrupted Binary
The binary itself may be corrupted.

**Check integrity**:
```bash
sha256sum /home/mitch/surge/build/surge_xt_products/surge-xt-cli
# Compare with a known-good build
```

### 4. Build Configuration Issue
The Surge build may have been compiled with incompatible flags or options.

**Likely culprits**:
- Optimizations too aggressive (`-O3`, `-march=native`)
- Missing runtime libraries
- Incorrect CMake configuration

### 5. Recent System Update
A recent OS/library update may have broken compatibility.

**Check**:
```bash
# Look for recent package updates
grep " upgrade " /var/log/dpkg.log | tail -20
grep " install " /var/log/dpkg.log | tail -20
```

## Debugging Steps

### Step 1: Get Core Dump

Enable core dumps to see exactly where it's crashing:

```bash
# Enable core dumps
ulimit -c unlimited

# Run surge
/home/mitch/surge/build/surge_xt_products/surge-xt-cli
# This will create a core file

# Analyze with gdb
gdb /home/mitch/surge/build/surge_xt_products/surge-xt-cli core
# In gdb:
# (gdb) bt        # Show backtrace
# (gdb) info registers
# (gdb) quit
```

### Step 2: Check Library Dependencies

```bash
ldd /home/mitch/surge/build/surge_xt_products/surge-xt-cli | grep "not found"
```

If any libraries are missing, install them:
```bash
sudo apt-get install <missing-library>
```

### Step 3: Run with Debugger

```bash
gdb /home/mitch/surge/build/surge_xt_products/surge-xt-cli
# In gdb:
# (gdb) run --all-midi-inputs
# (gdb) bt        # When it crashes, show backtrace
```

### Step 4: Try Rebuilding Surge

The binary may need to be rebuilt:

```bash
cd ~/surge
git pull  # Get latest code
rm -rf build  # Clean old build
mkdir build && cd build

# Configure with safe options
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DSURGE_BUILD_LV2=FALSE \
  -DSURGE_BUILD_VST3=FALSE \
  -DSURGE_BUILD_CLAP=FALSE

# Build
make -j$(nproc) surge-xt-cli

# Test
./surge_xt_products/surge-xt-cli --help
```

### Step 5: Try Pre-built Binary

Download an official release binary instead of building from source:

```bash
# Check Surge XT releases
# https://github.com/surge-synthesizer/surge/releases
```

## Immediate Workarounds

### Workaround 1: Use GUI Version

If only CLI is broken, try the GUI version:

```bash
/home/mitch/surge/build/src/surge-xt/surge-xt_artefacts/Release/Standalone/Surge\ XT
```

(Requires VNC/X11, not ideal for headless)

### Workaround 2: Use Different Synthesizer

Consider alternative MPE-capable synths for Pi:
- Vital (if ARM build available)
- Dexed
- ZynAddSubFX

### Workaround 3: Rollback System

If recent update caused this:

```bash
# Check what was updated recently
grep " upgrade " /var/log/dpkg.log | tail -50

# Rollback specific packages
sudo apt-get install <package>=<old-version>
```

## Resolution Plan

1. **[PRIORITY]** Run `gdb` to get backtrace showing exact crash location
2. Check `ldd` output for missing/broken libraries
3. Try rebuilding Surge with conservative compiler flags
4. If rebuild fails, try official pre-built binary
5. If all fails, consider alternative synthesizer

---

## Notes for Future Reference

**What worked before this issue appeared**:
- Unknown - user reported "sporadic issues" with Surge CLI
- May have always been broken, just masked by restart loops

**Audio robustness changes completed**:
- ✅ 4-tier audio fallback system implemented
- ✅ USB DAC → headphone jack fallback ready
- ✅ Device detection script working perfectly
- ❌ **BLOCKED**: Cannot test because Surge won't launch

**The audio detection infrastructure is solid and ready to use once Surge binary is fixed.**

---

**Last Updated**: 2025-12-27 19:57 EST
**Next Action**: Run gdb backtrace to identify crash location
