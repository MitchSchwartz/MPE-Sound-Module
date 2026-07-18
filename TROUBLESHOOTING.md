# Boot Screen Troubleshooting Guide

## Issue 1: No Boot Animation Showing

### Possible Causes:
1. Service failed to start
2. Display initialization error
3. Python dependencies missing

### Steps to Diagnose:

```bash
# 1. Check if service is enabled and running
sudo systemctl status boot-animation.service

# 2. Try running manually to see errors
cd /home/mitch/MPE-Module
python3 boot_animation.py --test

# 3. Check logs
journalctl -u boot-animation.service --since "10 minutes ago"

# 4. Check if service file was reloaded
sudo systemctl daemon-reload
sudo systemctl restart boot-animation.service
```

### Fix:
```bash
# Reload systemd configuration
sudo systemctl daemon-reload

# Enable and start the service
sudo systemctl enable boot-animation.service
sudo systemctl restart boot-animation.service
```

---

## Issue 2: No Sound / Patches Not Loading

### Root Cause:
**Surge XT CLI is not running or not responding on OSC port 6667**

This causes:
- PatchLoader initialization to fail
- `self.loader` to be set to `None`
- All patch loading attempts to fail silently
- No sound because Surge isn't running

### Steps to Diagnose:

```bash
# 1. Check if Surge service is running
sudo systemctl status surge-xt-cli.service

# 2. Check if Surge process exists
pgrep -fa surge

# 3. Check if OSC port is listening
netstat -ln | grep 6667

# 4. Try starting Surge manually
cd /home/mitch/MPE-Module
./scripts/start-surge-cli.sh

# 5. Check Surge logs
journalctl -u surge-xt-cli.service --since "10 minutes ago"
```

### Fix:

```bash
# Reload systemd and restart Surge service
sudo systemctl daemon-reload
sudo systemctl restart surge-xt-cli.service

# Wait 3 seconds for Surge to initialize
sleep 3

# Restart patch browser
sudo systemctl restart patch-browser.service

# Check all services are running
systemctl status surge-xt-cli.service
systemctl status patch-browser.service
```

---

## Complete System Recovery

If both issues are present, do a full restart:

```bash
# Stop all services
sudo systemctl stop patch-browser.service
sudo systemctl stop boot-animation.service
sudo systemctl stop surge-xt-cli.service

# Reload configuration
sudo systemctl daemon-reload

# Start in correct order
sudo systemctl start surge-xt-cli.service
sleep 3  # Give Surge time to start

sudo systemctl start boot-animation.service
sleep 2

sudo systemctl start patch-browser.service

# Check status
systemctl status surge-xt-cli.service --no-pager
systemctl status boot-animation.service --no-pager
systemctl status patch-browser.service --no-pager
```

---

## Verify Everything is Working

```bash
# 1. Surge should be running
pgrep -fa surge
# Expected output: Process ID and command line

# 2. OSC port should be listening
netstat -ln | grep 6667
# Expected output: tcp ... 0.0.0.0:6667 ... LISTEN

# 3. Check patch browser logs
journalctl -u patch-browser.service -f
# Should see: "Quick-loading last patch..." or "Scanning patches..."

# 4. Test boot animation
python3 /home/mitch/MPE-Module/boot_animation.py --test
# Should show progress bar on OLED for 10 seconds
```

---

## Common Errors and Solutions

### Error: "Patch loader not initialized"
**Cause**: Surge XT is not running or OSC port not available
**Fix**: Restart surge-xt-cli.service (see above)

### Error: "Display initialization failed"
**Cause**: I2C device not available or permission issue
**Fix**:
```bash
# Check I2C is enabled
ls -la /dev/i2c-1
# Should show: crw-rw---- 1 root i2c ...

# Add user to i2c group if needed
sudo usermod -a -G i2c mitch
```

### Error: "Background scan failed"
**Cause**: Patch directories don't exist
**Fix**:
```bash
# Create missing directories
mkdir -p ~/Documents/"Surge XT"/Patches
```

### Error: Boot animation shows but patches don't load
**Cause**: Timing issue - patch browser starting before Surge is ready
**Fix**: Increase the sleep delay in patch-browser.service:
```bash
# Edit: /home/mitch/MPE-Module/config/patch-browser.service
# Change: ExecStartPre=/bin/sleep 2
# To: ExecStartPre=/bin/sleep 3

sudo systemctl daemon-reload
sudo systemctl restart patch-browser.service
```

---

## Testing After Fixes

```bash
# Full reboot test
sudo reboot

# Watch boot sequence
# - Boot animation should show with progress bar
# - Last patch should load within 2-3 seconds
# - Sound should play when you press keys

# If still no sound:
journalctl -u surge-xt-cli.service --since boot
# Look for errors in Surge startup
```

---

## Quick Status Check Script

Save this as `check-status.sh` and run it:

```bash
#!/bin/bash
echo "=== Service Status ==="
systemctl is-active surge-xt-cli.service && echo "✓ Surge running" || echo "✗ Surge NOT running"
systemctl is-active boot-animation.service && echo "✓ Boot animation running" || echo "✗ Boot animation NOT running"
systemctl is-active patch-browser.service && echo "✓ Patch browser running" || echo "✗ Patch browser NOT running"

echo -e "\n=== OSC Port ==="
netstat -ln | grep -q 6667 && echo "✓ OSC port 6667 listening" || echo "✗ OSC port NOT listening"

echo -e "\n=== Surge Process ==="
pgrep -fa surge || echo "✗ No Surge process found"
```

Run with: `bash check-status.sh`
