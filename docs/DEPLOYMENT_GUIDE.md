# Deployment Guide: Audio Robustness Upgrade

**Purpose**: Deploy the new 4-tier audio fallback system to your Raspberry Pi
**Time Required**: ~15 minutes
**Risk Level**: Low (easily reversible)

---

## Pre-Deployment Checklist

- [ ] SSH access to Pi is working: `ssh -i ~/.ssh/surge_pi_key mitch@surge.local`
- [ ] Current system is working (verify with: `sudo systemctl status surge-xt-cli`)
- [ ] You have a backup plan (instructions below)

---

## Step 1: Backup Current Configuration

SSH into your Pi and create backups:

```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Create backup directory
mkdir -p ~/backups

# Backup current startup script
cp ~/start-surge-cli.sh ~/backups/start-surge-cli.sh.backup-$(date +%Y%m%d)

# Backup current service file
sudo cp /etc/systemd/system/surge-xt-cli.service ~/backups/surge-xt-cli.service.backup-$(date +%Y%m%d)

# Verify backups
ls -la ~/backups/

echo "✅ Backups created"
```

---

## Step 2: Create Scripts Directory

```bash
# Create scripts directory if it doesn't exist
mkdir -p ~/scripts

# Verify
ls -la ~/scripts/
```

---

## Step 3: Upload New Scripts

From your development machine (where this repo is cloned):

```bash
# Navigate to the repo directory
cd "c:\Users\mitch\GitHub\MPE Module"

# Upload detection script
scp -i ~/.ssh/surge_pi_key scripts/detect-audio-device.sh mitch@surge.local:~/scripts/

# Upload test script
scp -i ~/.ssh/surge_pi_key scripts/test-audio-detection.sh mitch@surge.local:~/scripts/

# Upload updated startup script
scp -i ~/.ssh/surge_pi_key scripts/start-surge-cli.sh mitch@surge.local:~/scripts/

# Upload updated service file
scp -i ~/.ssh/surge_pi_key config/surge-xt-cli.service mitch@surge.local:~/
```

---

## Step 4: Set Executable Permissions

Back on the Pi:

```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Make scripts executable
chmod +x ~/scripts/detect-audio-device.sh
chmod +x ~/scripts/test-audio-detection.sh
chmod +x ~/scripts/start-surge-cli.sh

# Verify permissions
ls -la ~/scripts/*.sh
```

---

## Step 5: Test Audio Detection

**Before deploying**, test the detection script manually:

```bash
# Run the test script
~/scripts/test-audio-detection.sh

# You should see output like:
# ✅ Detection successful!
# DEVICE_ID=X.XX
# DEVICE_NAME=Sound Blaster Play! 3 Front
# TIER=1
```

**Expected Results**:
- **Tier 1**: If Sound Blaster is connected
- **Tier 2**: If another USB DAC is connected
- **Tier 3**: If using Pi's headphone jack
- **Tier 4**: First available device

If detection fails, **STOP** and troubleshoot before proceeding.

---

## Step 6: Update Startup Script Link

```bash
# Remove old startup script and create symlink to new one
rm ~/start-surge-cli.sh
ln -s ~/scripts/start-surge-cli.sh ~/start-surge-cli.sh

# Verify symlink
ls -la ~/start-surge-cli.sh
# Should show: start-surge-cli.sh -> /home/mitch/scripts/start-surge-cli.sh
```

---

## Step 7: Update Systemd Service

```bash
# Copy new service file
sudo cp ~/surge-xt-cli.service /etc/systemd/system/

# Reload systemd to pick up changes
sudo systemctl daemon-reload

# Verify service file was updated
systemctl cat surge-xt-cli

# Look for these new lines:
#   After=sound.target network.target
#   Wants=sound.target
#   RestartSec=10
#   StartLimitBurst=5
```

---

## Step 8: Restart Service and Test

```bash
# Stop the current service
sudo systemctl stop surge-xt-cli

# Clear any old logs
echo "" > ~/surge-cli.log

# Start with new configuration
sudo systemctl start surge-xt-cli

# Wait 5 seconds
sleep 5

# Check status
sudo systemctl status surge-xt-cli

# Check logs
tail -20 ~/surge-cli.log
```

**Look for in the log**:
```
Starting Surge XT CLI...
Selected audio device: X.XX
  Name: <device name>
  Tier: <1-4>
Surge XT CLI started with PID XXXXX
```

---

## Step 9: Verify Audio Output

1. **Connect your Roli Seaboard**
2. **Play some notes**
3. **Verify you hear sound**

If no sound:
```bash
# Check service status
sudo systemctl status surge-xt-cli

# Check detailed logs
sudo journalctl -u surge-xt-cli -n 50

# Check which device was selected
tail ~/surge-cli.log
```

---

## Step 10: Test Reboot

The ultimate test - does it survive a reboot?

```bash
# Reboot the Pi
sudo reboot

# Wait ~30 seconds, then reconnect
ssh -i ~/.ssh/surge_pi_key mitch@surge.local

# Check if service started automatically
sudo systemctl status surge-xt-cli

# Check logs
tail -30 ~/surge-cli.log

# Test audio by playing Roli
```

---

## Step 11: Install USB Hot-Plug Support (Optional)

This makes Surge automatically restart when you plug/unplug USB audio devices:

```bash
# Upload udev rules file
# (From development machine)
scp -i ~/.ssh/surge_pi_key config/99-usb-audio.rules mitch@surge.local:~/

# Install udev rules
# (On the Pi)
sudo cp ~/99-usb-audio.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules

# Test by unplugging/replugging USB DAC
# Watch logs:
sudo journalctl -u surge-xt-cli -f
```

---

## Troubleshooting

### Problem: Detection script fails

**Check**:
```bash
~/scripts/detect-audio-device.sh ~/surge/build/surge_xt_products/surge-xt-cli
```

**Common issues**:
- Surge CLI path wrong → Update path in scripts
- No audio devices found → Check `aplay -l`
- Parsing error → Check surge output format: `surge-xt-cli --list-devices`

### Problem: Service won't start

**Check**:
```bash
sudo journalctl -u surge-xt-cli -n 100 --no-pager

# Look for:
# - "CRITICAL - Audio detection failed"
# - Exit codes
# - Permission errors
```

**Fix**:
```bash
# Verify script permissions
ls -la ~/scripts/

# Verify paths
cat ~/scripts/start-surge-cli.sh | grep SURGE_CLI
```

### Problem: No sound output

**Check**:
```bash
# Which device was selected?
tail ~/surge-cli.log | grep "Selected audio device"

# Is that device valid?
aplay -l

# Test device directly
speaker-test -D plughw:0,0 -c 2 -t wav
```

### Problem: Tier 3 (headphone jack) selected but USB DAC is connected

**Possible causes**:
- USB DAC name doesn't contain "USB" → Check device name in `aplay -l`
- Device is listed but not initialized → Unplug/replug USB DAC
- Detection script needs tuning → Add device name to Tier 1 or 2 patterns

---

## Rollback Procedure

If things go wrong, revert to backups:

```bash
# Stop service
sudo systemctl stop surge-xt-cli

# Restore old startup script
cp ~/backups/start-surge-cli.sh.backup-* ~/start-surge-cli.sh

# Restore old service file
sudo cp ~/backups/surge-xt-cli.service.backup-* /etc/systemd/system/surge-xt-cli.service

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl start surge-xt-cli

# Verify
sudo systemctl status surge-xt-cli
```

---

## Success Criteria

✅ **Deployment successful if**:
- Service starts without errors
- Audio device is auto-detected (check tier in logs)
- Sound output works
- Service survives reboot
- Logs show device selection reasoning

---

## Post-Deployment

### Monitor for a few days

```bash
# Check logs periodically
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "tail -50 ~/surge-cli.log"

# Check service uptime
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "sudo systemctl status surge-xt-cli"
```

### Test USB DAC hot-plug (if rules installed)

1. Unplug USB DAC
2. Wait 15 seconds
3. Check logs: `sudo journalctl -u surge-xt-cli -n 20`
4. Should see service restart and Tier 3 (headphone jack) selection
5. Plug USB DAC back in
6. Should see service restart and Tier 1/2 selection

---

## Next Steps

Once stable, update your documentation:

- [ ] Update [CURRENT_STATE.md](CURRENT_STATE.md) with new audio detection approach
- [ ] Update [PROJECT_PLAN.md](PROJECT_PLAN.md) to mark Phase 1 robustness complete
- [ ] Document any device-specific tuning needed for your setup

---

**Questions or Issues?**

Check the detailed plan: [AUDIO_ROBUSTNESS_PLAN.md](AUDIO_ROBUSTNESS_PLAN.md)
