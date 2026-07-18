# Getting Surge XT on ARM64 (Raspberry Pi)

## Current Situation

Official Surge XT releases **do not include ARM64 binaries** (as of 2025). You have two options:

1. **Use nightly builds** (easiest - if available)
2. **Build from source** (takes ~30-45 min on Pi 4/5)

## Option 1: Nightly Builds (Recommended)

Check the [Surge XT Nightly Releases](https://surge-synthesizer.github.io/nightly_XT/) page.

Look for ARM64/aarch64 builds at the [Open Build Service](https://build.opensuse.org/package/show/home:kill_it:surge-synth/surge-xt).

### If Nightly Build Available:

```bash
# Download the .deb or .tar.gz for aarch64
wget <url-to-arm64-build>

# If .deb:
sudo dpkg -i surge-xt-*-arm64.deb
sudo apt-get install -f  # Fix any dependencies

# If .tar.gz:
tar xzf surge-xt-*-arm64.tar.gz
sudo cp -r Surge-XT /usr/local/bin/
```

## Option 2: Build from Source (Tested on Pi 4)

Building takes about 30-45 minutes but gives you the latest version.

### Prerequisites

Already installed by `install.sh`:
- build-essential
- cmake
- git
- Development libraries (cairo, xcb, etc.)

### Build Steps

```bash
# Clone Surge XT repository
cd ~
git clone https://github.com/surge-synthesizer/surge.git
cd surge

# Checkout latest stable tag (or use main for bleeding edge)
git checkout release_xt/1.3.4  # Replace with latest version

# Create build directory
mkdir build
cd build

# Configure with CMake for ARM
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DLINUX_ON_ARM=TRUE \
  -DSURGE_BUILD_LV2=FALSE \
  -DSURGE_BUILD_VST3=FALSE \
  -DSURGE_BUILD_CLAP=FALSE \
  -DSURGE_BUILD_STANDALONE=TRUE

# Build (use all CPU cores)
make -j$(nproc)

# Install
sudo make install

# Or copy manually
sudo cp surge-xt-standalone /usr/local/bin/Surge-XT
```

### Build Time Estimates

| Pi Model | Cores | Build Time |
|----------|-------|------------|
| Pi 4 (4GB) | 4 | ~30-45 min |
| Pi 5 (4GB) | 4 | ~20-30 min |
| Pi 4 (with swap) | 4 | ~40-50 min |

### Reduce Build Time

If you only need standalone (not plugins):

```bash
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DLINUX_ON_ARM=TRUE \
  -DSURGE_BUILD_LV2=FALSE \
  -DSURGE_BUILD_VST3=FALSE \
  -DSURGE_BUILD_CLAP=FALSE \
  -DSURGE_BUILD_STANDALONE=TRUE \
  -DSURGE_BUILD_TESTRUNNER=FALSE

make surge-xt-standalone -j$(nproc)
```

This skips plugins and tests, building only what you need.

## Option 3: Pre-compiled from Community

### Arch Linux ARM Packages

If you were running Arch Linux ARM, you could use:
- [surge-xt-standalone](https://archlinuxarm.org/packages/aarch64/surge-xt-standalone)

But since we're using Raspberry Pi OS (Debian-based), this doesn't help directly.

### Patchbox OS / Other Distros

Some audio-focused distros may have pre-built ARM packages. Check:
- [Patchbox OS](https://blokas.io/patchbox-os/)
- KXStudio repositories

## Verification

After installation (any method):

```bash
# Check binary exists
which Surge-XT
# Should show: /usr/local/bin/Surge-XT

# Check it runs (requires X11)
Surge-XT --help

# Check architecture
file /usr/local/bin/Surge-XT
# Should show: ELF 64-bit LSB executable, ARM aarch64
```

## Troubleshooting Build

### Out of Memory

If build fails with "out of memory":

```bash
# Reduce parallel jobs
make -j2  # Instead of -j4

# Or add swap space
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Missing Dependencies

```bash
# If CMake complains about missing libs
sudo apt-get install -y \
  libcairo2-dev \
  libxkbcommon-x11-dev \
  libxkbcommon-dev \
  libxcb-cursor-dev \
  libxcb-keysyms1-dev \
  libxcb-util-dev \
  libxrandr-dev \
  libxinerama-dev \
  libxcursor-dev \
  libasound2-dev \
  libjack-jackd2-dev \
  libfreetype6-dev \
  libglu1-mesa-dev
```

These should already be installed by `install.sh`.

### Build Fails

```bash
# Clean and retry
cd ~/surge/build
rm -rf *
cmake .. (same flags as before)
make -j2  # Use fewer cores
```

## Recommended Approach

**For Milestone 1 testing:**

1. **Check nightly builds first** - Fastest if available
2. **If no nightlies, build from source** - One-time 30-45 min investment
3. **Build while doing other setup** - Start build, configure JACK in parallel

**Building in background:**

```bash
# SSH into Pi
ssh pi@pisurge.local

# Start build in screen/tmux
screen -S surge-build

# Run build commands
cd ~/surge/build
cmake .. (flags)
make -j4

# Detach: Ctrl+A, D
# Reattach later: screen -r surge-build
```

## Post-Build Optimization

Once built, you can:

```bash
# Strip debug symbols (reduces size)
sudo strip /usr/local/bin/Surge-XT

# Backup the binary
cp /usr/local/bin/Surge-XT ~/surge-xt-backup

# If you want to rebuild later, keep the source
# Or delete to save space:
rm -rf ~/surge
```

## Alternative: Test on x86 First

If you have an x86 Linux machine or VM:

1. Install Surge XT from official release
2. Test MPE workflow
3. Validate presets/settings
4. Export config
5. Then build ARM version with known-good config

This way you know it works before spending 45 min building.

## Next Steps

After getting Surge XT binary:

1. Place in `/usr/local/bin/Surge-XT`
2. Make executable: `sudo chmod +x /usr/local/bin/Surge-XT`
3. Continue with [README.md](../README.md) Milestone 1 testing

## Sources

- [Surge XT Nightly Releases](https://surge-synthesizer.github.io/nightly_XT/)
- [Surge GitHub Repository](https://github.com/surge-synthesizer/surge)
- [Arch Linux ARM Packages](https://archlinuxarm.org/packages/aarch64/surge-xt-standalone)
- [Open Build Service](https://build.opensuse.org/package/show/home:kill_it:surge-synth/surge-xt)
