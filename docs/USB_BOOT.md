# USB Boot Guide for Pi-Surge-MPE

## Why Boot from USB?

**Advantages**:
- Faster than SD card (especially with SSD)
- More reliable (SSDs have better endurance)
- Easy to swap between configurations
- Can test without touching your Zynthian SD card

**Disadvantages**:
- Requires Pi 4/5 (Pi 3 needs bootloader update)
- Slightly more complex initial setup

## Requirements

- Raspberry Pi 4 or 5 (Pi 4 may need bootloader update)
- USB drive (thumb drive or SSD)
  - Minimum 16GB (32GB recommended)
  - USB 3.0 for best performance
  - SSD recommended for speed/reliability

## Enable USB Boot (One-Time Setup)

### Check if USB Boot is Already Enabled

1. Boot your Pi from SD card (any SD card)
2. Run:
   ```bash
   sudo raspi-config
   ```
3. Navigate to: `Advanced Options` > `Boot Order`
4. Select: `USB Boot`
5. Reboot

Alternatively, check the bootloader:
```bash
sudo rpi-eeprom-config
```

Look for: `BOOT_ORDER=0xf41` (USB boot enabled)

### If Not Enabled (Pi 4 only)

Pi 5 has USB boot enabled by default. Pi 4 may need update:

```bash
# Update bootloader
sudo apt update
sudo apt full-upgrade -y
sudo rpi-eeprom-update -d -a

# Reboot
sudo reboot

# After reboot, enable USB boot
sudo raspi-config
# Advanced Options > Boot Order > USB Boot
```

## Flash Pi OS Lite to USB Drive

### Method 1: Raspberry Pi Imager (Recommended)

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Insert USB drive into your computer
3. In Imager:
   - **OS**: Raspberry Pi OS Lite (64-bit)
   - **Storage**: Select your USB drive
   - **Gear icon** (settings):
     - Set hostname: `pisurge`
     - Enable SSH (use password auth)
     - Set username: `pi`
     - Set password: (your choice)
     - Configure WiFi (if needed)
     - Set locale/timezone
4. Write
5. Remove USB drive

### Method 2: Manual (if you have existing SD card image)

```bash
# On Windows (using Win32DiskImager or Rufus)
# Or on Linux/Mac:
sudo dd if=pisurge.img of=/dev/sdX bs=4M status=progress
sync
```

## Boot from USB

### First Boot

1. Remove SD card from Pi (important!)
2. Insert USB drive into USB 3.0 port (blue port on Pi 4)
3. Power on Pi
4. Wait for boot (~30-60s first time)
5. SSH in:
   ```bash
   ssh pi@pisurge.local
   # Or if mDNS doesn't work:
   ssh pi@<ip-address>
   ```

### If Pi Won't Boot from USB

**Troubleshooting**:

1. **Try different USB port**
   - Pi 4: Use USB 3.0 (blue) port
   - Pi 5: Any USB 3.0 port

2. **Check USB drive compatibility**
   - Some USB drives don't work
   - Try a different drive
   - SSDs are most reliable

3. **Boot from SD to check**
   - Insert SD card (any bootable SD)
   - Check dmesg for USB errors:
     ```bash
     dmesg | grep usb
     ```

4. **Update bootloader** (if Pi 4)
   - See "Enable USB Boot" section above

## Installation on USB-Booted Pi

Once booted from USB, follow the normal installation:

```bash
# Clone repo
cd ~
git clone <repo-url> pisurge
cd pisurge

# Run installer
chmod +x install.sh
./install.sh

# Follow README.md from here
```

Everything else is identical to SD card installation.

## Performance Comparison

### SD Card vs USB Drive vs SSD

| Storage | Boot Time | IOPS | Reliability | Cost |
|---------|-----------|------|-------------|------|
| SD Card (Class 10) | 30-40s | ~100 | Medium | $10 |
| USB 3.0 Flash | 25-35s | ~200 | Medium | $15 |
| USB 3.0 SSD | 20-25s | ~1000+ | High | $30+ |

**Recommendation**: USB 3.0 SSD for best performance and reliability.

### Recommended USB Drives

**Budget** (USB Flash):
- SanDisk Ultra Fit USB 3.1
- Samsung BAR Plus USB 3.1

**Performance** (SSD):
- Samsung T7 Portable SSD
- SanDisk Extreme Portable SSD
- Crucial X6 Portable SSD

## Switching Between Configurations

With USB boot, you can easily swap between:
- Zynthian SD card (your current setup)
- Pi-Surge-MPE USB drive (new test setup)

Just power off, swap drives, power on. No conflicts!

## Testing Workflow

### Recommended Approach

1. **Keep Zynthian SD card safe** (currently backing up)
2. **Use USB drive for Pi-Surge-MPE testing**
3. **Validate Milestone 1** on USB
4. **Once stable, choose**:
   - Keep both (swap as needed)
   - Commit to Pi-Surge-MPE (repurpose SD for something else)
   - Keep Zynthian (if you need it for other projects)

### Parallel Testing

You can even have both systems and A/B test:
- Day 1: Boot Zynthian (SD card) for reference
- Day 2: Boot Pi-Surge-MPE (USB) to compare
- Decide which works better for your workflow

## USB Boot Limitations

**None for this project!** USB boot works perfectly for:
- JACK audio
- Surge XT
- GPIO encoders
- All features of Pi-Surge-MPE

## Backup Strategy

With USB boot:

```bash
# Backup entire USB drive (from your computer)
# Windows: Win32DiskImager > Read
# Linux/Mac:
sudo dd if=/dev/sdX of=pisurge-backup.img bs=4M status=progress

# Restore:
sudo dd if=pisurge-backup.img of=/dev/sdX bs=4M status=progress
```

Or use your SD backup workflow - same process.

## FAQ

### Q: Can I boot from SD and use USB as data storage?
**A**: Yes, but not needed for this project. Everything fits on the boot drive.

### Q: Will USB boot affect audio performance?
**A**: No. Once loaded, audio runs in RAM. Storage speed doesn't matter during playback.

### Q: Can I use a USB hub?
**A**: Not for booting. Boot drive must be directly connected. After boot, hub is fine for other devices.

### Q: What about USB 2.0 drives?
**A**: Will work but slower. USB 3.0 recommended for better boot times.

### Q: Can I clone my SD to USB?
**A**: Yes, after installation:
```bash
# From another Linux machine with both drives connected:
sudo dd if=/dev/sdX of=/dev/sdY bs=4M status=progress
```

## Next Steps

1. Flash Pi OS Lite to USB drive (use Raspberry Pi Imager)
2. Boot Pi from USB
3. Follow [README.md](../README.md) as normal
4. Test Milestone 1 without touching your Zynthian SD card

Your Zynthian backup is safe, and you can test Pi-Surge-MPE in parallel!
