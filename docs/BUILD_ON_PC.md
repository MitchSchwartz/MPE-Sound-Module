# Build Surge XT on Your PC (Cross-Compile for Pi)

**Why:** Your PC builds in ~5-10 minutes vs 30-45 minutes on Pi

## Prerequisites

You need **WSL2 with Ubuntu** on Windows.

### Install WSL2 (if you don't have it)

**PowerShell as Administrator:**
```powershell
wsl --install -d Ubuntu
```

Reboot, then set up Ubuntu (username/password).

## Build Process (WSL2)

### One-Time Setup (~5 minutes)

```bash
# Open WSL2 (Ubuntu)
wsl

# Install cross-compile toolchain
sudo apt update
sudo apt install -y \
  cmake \
  g++-aarch64-linux-gnu \
  gcc-aarch64-linux-gnu \
  git \
  build-essential \
  pkg-config

# Clone Surge
cd ~
git clone https://github.com/surge-synthesizer/surge.git
cd surge
git checkout main  # Or release_xt/1.3.4 for stable
```

### Build Surge XT (~5-10 minutes)

```bash
cd ~/surge
mkdir -p build-arm64
cd build-arm64

# Configure for ARM64 cross-compile
cmake .. \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
  -DCMAKE_BUILD_TYPE=Release \
  -DLINUX_ON_ARM=TRUE \
  -DSURGE_BUILD_LV2=FALSE \
  -DSURGE_BUILD_VST3=FALSE \
  -DSURGE_BUILD_CLAP=FALSE \
  -DSURGE_BUILD_STANDALONE=TRUE \
  -DSURGE_SKIP_JUCE_FOR_RACK=TRUE

# Build (uses all your CPU cores)
make -j$(nproc) surge-xt-standalone
```

**Build time on modern PC:** 5-10 minutes

### Copy Binary to Windows

```bash
# Binary is at: ~/surge/build-arm64/surge-xt/Surge-XT

# Copy to Windows (accessible from File Explorer)
cp ~/surge/build-arm64/surge-xt/Surge-XT /mnt/c/Users/mitch/Desktop/Surge-XT-arm64

# Or create a tarball
cd ~/surge/build-arm64
tar czf surge-xt-arm64.tar.gz surge-xt/Surge-XT
cp surge-xt-arm64.tar.gz /mnt/c/Users/mitch/Desktop/
```

Now `Surge-XT-arm64` is on your Windows Desktop!

### Transfer to Pi

**From PowerShell:**
```powershell
# Copy to Pi via SCP
scp C:\Users\mitch\Desktop\Surge-XT-arm64 mitch@surge.local:~

# Or if you made tarball
scp C:\Users\mitch\Desktop\surge-xt-arm64.tar.gz mitch@surge.local:~
```

**On Pi (via SSH):**
```bash
# If you copied the binary directly
sudo cp ~/Surge-XT-arm64 /usr/local/bin/Surge-XT
sudo chmod +x /usr/local/bin/Surge-XT

# Or if you copied tarball
tar xzf ~/surge-xt-arm64.tar.gz
sudo cp surge-xt/Surge-XT /usr/local/bin/
sudo chmod +x /usr/local/bin/Surge-XT

# Verify
which Surge-XT
file /usr/local/bin/Surge-XT
# Should show: ELF 64-bit LSB executable, ARM aarch64
```

## Rebuild When Updating

```bash
# In WSL2
cd ~/surge
git pull origin main
cd build-arm64
make -j$(nproc) surge-xt-standalone

# Copy to Desktop again
cp surge-xt/Surge-XT /mnt/c/Users/mitch/Desktop/Surge-XT-arm64

# SCP to Pi (from PowerShell)
scp C:\Users\mitch\Desktop\Surge-XT-arm64 mitch@surge.local:~

# On Pi: backup old, install new
sudo cp /usr/local/bin/Surge-XT /usr/local/bin/Surge-XT.old
sudo cp ~/Surge-XT-arm64 /usr/local/bin/Surge-XT
sudo chmod +x /usr/local/bin/Surge-XT
systemctl --user restart surge.service
```

## Alternative: Build on Pi (Fallback)

If cross-compile has issues, fall back to building on Pi:

```bash
# On Pi (via SSH)
cd ~
git clone https://github.com/surge-synthesizer/surge.git
cd surge
git checkout main
mkdir build && cd build

cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DLINUX_ON_ARM=TRUE \
  -DSURGE_BUILD_STANDALONE=TRUE

# Takes 30-45 min
make -j4
sudo make install
```

## Troubleshooting Cross-Compile

### CMake can't find libraries

Cross-compiling might complain about missing ARM libraries. If this happens:

**Just build on the Pi instead** - it's slower but guaranteed to work.

Or install ARM libraries in WSL2 (more complex, not worth it for one build).

### Binary won't run on Pi

```bash
# Check architecture
file Surge-XT-arm64
# Must show: ARM aarch64

# If it shows x86_64, cross-compile failed
# Rebuild on Pi instead
```

## Recommendation

**Try cross-compile first** (~10 min setup + 5 min build)
**If it fails**, just build on Pi (~45 min)

Either way, you only build once (or rarely). Not a big deal!

## Quick Reference

**Build on PC (WSL2):**
```bash
cd ~/surge/build-arm64
make -j$(nproc) surge-xt-standalone
cp surge-xt/Surge-XT /mnt/c/Users/mitch/Desktop/Surge-XT-arm64
```

**Transfer to Pi:**
```powershell
scp C:\Users\mitch\Desktop\Surge-XT-arm64 mitch@surge.local:~
```

**Install on Pi:**
```bash
sudo cp ~/Surge-XT-arm64 /usr/local/bin/Surge-XT
sudo chmod +x /usr/local/bin/Surge-XT
```

Done! 🚀
