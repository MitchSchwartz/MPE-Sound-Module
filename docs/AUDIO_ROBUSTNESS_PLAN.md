# Audio Robustness Plan: Automatic USB DAC Detection with Headphone Jack Fallback

**Created**: 2025-12-27
**Problem**: Surge CLI crashes sporadically due to audio output connection issues
**Goal**: Automatically connect to any available USB DAC, with fallback to Raspberry Pi headphone jack

---

## Current Situation Analysis

### What's Working
- Audio device auto-detection for Sound Blaster Play! 3
- Hardcoded fallback to device `0.23` if detection fails
- Logging of audio device selection decisions
- Systemd auto-restart on failure (5-second delay)

### Current Problems
1. **Too Specific**: Only searches for "Sound Blaster Play! 3" by name
2. **Brittle Fallback**: Falls back to hardcoded `0.23` which may not exist
3. **No Headphone Jack Fallback**: No fallback to built-in Pi audio
4. **Single Retry Logic**: If wrong device is chosen, relies on crash/restart cycle
5. **No Device Validation**: Doesn't test if chosen device actually works before launching

---

## Solution Architecture

### Multi-Tier Fallback Strategy

```
┌─────────────────────────────────────────────┐
│ Tier 1: Preferred USB DAC (Sound Blaster)  │
│         - Search by name pattern            │
│         - Filter out unusable variants      │
└──────────────┬──────────────────────────────┘
               │ (if not found)
               ▼
┌─────────────────────────────────────────────┐
│ Tier 2: ANY USB Audio Device               │
│         - Enumerate all USB audio outputs   │
│         - Exclude internal/HDMI devices     │
│         - Pick first valid USB device       │
└──────────────┬──────────────────────────────┘
               │ (if not found)
               ▼
┌─────────────────────────────────────────────┐
│ Tier 3: Raspberry Pi Headphone Jack        │
│         - Search for "Headphones" device    │
│         - Or bcm2835 ALSA device            │
└──────────────┬──────────────────────────────┘
               │ (if not found)
               ▼
┌─────────────────────────────────────────────┐
│ Tier 4: Any Available Output               │
│         - Use first listed output device    │
│         - Last resort fallback              │
└─────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: Enhanced Device Detection Script

**File**: `scripts/detect-audio-device.sh` (NEW)

Create a standalone audio device detection script that:

1. **Lists all available audio devices**
   ```bash
   surge-xt-cli --list-devices 2>&1
   ```

2. **Prioritizes devices in order**:
   - Tier 1: USB DAC by preferred name (Sound Blaster Play! 3)
   - Tier 2: Any USB audio device (excluding bad patterns)
   - Tier 3: Raspberry Pi headphone jack (bcm2835 Headphones)
   - Tier 4: First available output device

3. **Validates device selection**:
   - Ensures device ID is not empty
   - Logs decision rationale
   - Returns device ID to stdout

4. **Robust parsing**:
   - Handle varying surge output formats
   - Extract device IDs correctly
   - Filter out problematic device types

**Output Format**:
```
DEVICE_ID=<number>
DEVICE_NAME=<description>
TIER=<1-4>
```

### Phase 2: Update start-surge-cli.sh

**File**: `scripts/start-surge-cli.sh` (MODIFIED)

Replace current detection logic with call to new detection script:

```bash
#!/bin/bash
# Surge XT CLI - Headless startup script with robust audio fallback

SURGE_CLI="/home/mitch/surge/build/surge_xt_products/surge-xt-cli"
INIT_PATCH="/home/mitch/surge/resources/data/patches_factory/Keys/Church.fxp"
LOG_FILE="/home/mitch/surge-cli.log"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

echo "$(date): Starting Surge XT CLI..." >> "$LOG_FILE"

# Use robust audio detection script
AUDIO_RESULT=$("$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI")

if [ $? -ne 0 ]; then
    echo "$(date): CRITICAL - Audio detection failed completely" >> "$LOG_FILE"
    exit 1
fi

# Parse detection results
AUDIO_DEVICE=$(echo "$AUDIO_RESULT" | grep "^DEVICE_ID=" | cut -d= -f2)
DEVICE_NAME=$(echo "$AUDIO_RESULT" | grep "^DEVICE_NAME=" | cut -d= -f2)
DEVICE_TIER=$(echo "$AUDIO_RESULT" | grep "^TIER=" | cut -d= -f2)

echo "$(date): Selected audio device: $AUDIO_DEVICE ($DEVICE_NAME) [Tier $DEVICE_TIER]" >> "$LOG_FILE"

# Launch Surge CLI with detected device
"$SURGE_CLI" \
  --all-midi-inputs \
  --mpe-enable \
  --mpe-pitch-bend-range=48 \
  --init-patch="$INIT_PATCH" \
  --audio-interface="$AUDIO_DEVICE" \
  --no-stdin \
  >> "$LOG_FILE" 2>&1 &

SURGE_PID=$!
echo "$(date): Surge XT CLI started with PID $SURGE_PID" >> "$LOG_FILE"
```

### Phase 3: Pre-Launch Validation (Optional Enhancement)

Add a test to verify audio device is accessible before launching:

```bash
# Test device accessibility with aplay
if ! aplay -D "hw:$AUDIO_DEVICE" /dev/zero -f S16_LE -r 48000 -c 2 -d 1 2>/dev/null; then
    echo "$(date): WARNING - Device $AUDIO_DEVICE failed test, trying next option" >> "$LOG_FILE"
    # Fall back to next tier
fi
```

*Note: May add latency to startup, evaluate if needed*

### Phase 4: Improved Service Configuration

**File**: `config/surge-xt-cli.service` (MODIFIED)

Add better failure handling:

```ini
[Unit]
Description=Surge XT CLI Synthesizer (Headless)
After=sound.target network.target
Wants=sound.target

[Service]
Type=forking
User=mitch
WorkingDirectory=/home/mitch
Environment="XDG_RUNTIME_DIR=/run/user/1000"
ExecStart=/home/mitch/scripts/start-surge-cli.sh
Restart=on-failure
RestartSec=10
StartLimitBurst=5
StartLimitIntervalSec=300
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Changes**:
- Increase `RestartSec` from 5 to 10 seconds (allow USB devices to stabilize)
- Add `StartLimitBurst=5` (max 5 restart attempts)
- Add `StartLimitIntervalSec=300` (within 5 minutes)
- Add `Wants=sound.target` (prefer sound system ready, but don't require)

### Phase 5: Diagnostic Enhancements

**File**: `scripts/test-audio-detection.sh` (NEW)

Create a testing script to manually verify audio detection without launching Surge:

```bash
#!/bin/bash
# Test audio device detection logic

SURGE_CLI="/home/mitch/surge/build/surge_xt_products/surge-xt-cli"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

echo "=== Testing Audio Device Detection ==="
echo ""

# Run detection
"$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI"

echo ""
echo "=== Available Devices (Raw) ==="
"$SURGE_CLI" --list-devices 2>&1 | grep "Output Audio Device"

echo ""
echo "=== ALSA Devices ==="
aplay -l
```

---

## Detailed Implementation: detect-audio-device.sh

```bash
#!/bin/bash
# Audio device detection with multi-tier fallback
# Returns: DEVICE_ID, DEVICE_NAME, TIER

SURGE_CLI="${1:-/home/mitch/surge/build/surge_xt_products/surge-xt-cli}"

# Get all output devices from surge
DEVICE_LIST=$("$SURGE_CLI" --list-devices 2>&1 | grep "Output Audio Device")

if [ -z "$DEVICE_LIST" ]; then
    echo "ERROR: No audio devices found" >&2
    exit 1
fi

# Tier 1: Preferred USB DAC (Sound Blaster Play! 3)
DEVICE=$(echo "$DEVICE_LIST" | \
    grep "Sound Blaster Play! 3" | \
    grep -v "Surround" | \
    grep -v "S/PDIF" | \
    grep -v "USB Stream" | \
    sed -n 's/.*\[\([0-9.]*\)\].*/\1/p' | \
    head -1)

if [ -n "$DEVICE" ]; then
    DEVICE_NAME=$(echo "$DEVICE_LIST" | grep "\[$DEVICE\]" | sed 's/.*Output Audio Device //' | sed 's/ \[.*//')
    echo "DEVICE_ID=$DEVICE"
    echo "DEVICE_NAME=$DEVICE_NAME"
    echo "TIER=1"
    exit 0
fi

# Tier 2: Any USB audio device
DEVICE=$(echo "$DEVICE_LIST" | \
    grep -i "usb" | \
    grep -v "Surround" | \
    grep -v "S/PDIF" | \
    grep -v "HDMI" | \
    grep -v "Stream" | \
    sed -n 's/.*\[\([0-9.]*\)\].*/\1/p' | \
    head -1)

if [ -n "$DEVICE" ]; then
    DEVICE_NAME=$(echo "$DEVICE_LIST" | grep "\[$DEVICE\]" | sed 's/.*Output Audio Device //' | sed 's/ \[.*//')
    echo "DEVICE_ID=$DEVICE"
    echo "DEVICE_NAME=$DEVICE_NAME"
    echo "TIER=2"
    exit 0
fi

# Tier 3: Raspberry Pi headphone jack
DEVICE=$(echo "$DEVICE_LIST" | \
    grep -E "(Headphones|bcm2835)" | \
    grep -v "HDMI" | \
    sed -n 's/.*\[\([0-9.]*\)\].*/\1/p' | \
    head -1)

if [ -n "$DEVICE" ]; then
    DEVICE_NAME=$(echo "$DEVICE_LIST" | grep "\[$DEVICE\]" | sed 's/.*Output Audio Device //' | sed 's/ \[.*//')
    echo "DEVICE_ID=$DEVICE"
    echo "DEVICE_NAME=$DEVICE_NAME"
    echo "TIER=3"
    exit 0
fi

# Tier 4: First available device (last resort)
DEVICE=$(echo "$DEVICE_LIST" | \
    sed -n 's/.*\[\([0-9.]*\)\].*/\1/p' | \
    head -1)

if [ -n "$DEVICE" ]; then
    DEVICE_NAME=$(echo "$DEVICE_LIST" | grep "\[$DEVICE\]" | sed 's/.*Output Audio Device //' | sed 's/ \[.*//')
    echo "DEVICE_ID=$DEVICE"
    echo "DEVICE_NAME=$DEVICE_NAME"
    echo "TIER=4"
    exit 0
fi

# Absolute failure
echo "ERROR: Could not detect any audio device" >&2
exit 1
```

---

## Testing Strategy

### Test Cases

1. **Scenario 1: USB DAC connected and working**
   - Expected: Tier 1 selection (Sound Blaster)
   - Verify: Check surge-cli.log for correct device selection

2. **Scenario 2: USB DAC disconnected**
   - Expected: Tier 3 selection (Headphone jack)
   - Verify: Audio plays through 3.5mm jack

3. **Scenario 3: Different USB DAC connected**
   - Expected: Tier 2 selection (Generic USB audio)
   - Verify: Audio works with new device

4. **Scenario 4: Multiple USB DACs present**
   - Expected: Tier 1 if Sound Blaster present, else Tier 2 first USB
   - Verify: Correct prioritization in logs

5. **Scenario 5: Boot with no USB DAC, then plug in**
   - Expected: Starts on headphone jack, stays there (no hot-swap)
   - Verify: Systemd restart required for USB detection
   - Future enhancement: udev rules for USB audio hot-plug

### Test Commands

```bash
# Manual test of detection script
./scripts/detect-audio-device.sh

# Dry-run startup script (add debug mode)
DEBUG=1 ./scripts/start-surge-cli.sh

# Check service logs
sudo journalctl -u surge-xt-cli -f

# List actual ALSA devices
aplay -l

# Check surge device list
~/surge/build/surge_xt_products/surge-xt-cli --list-devices
```

---

## Rollout Plan

### Step 1: Development & Testing (Local)
- [ ] Create `detect-audio-device.sh` script
- [ ] Test detection logic with `--list-devices` output samples
- [ ] Verify all tiers work correctly
- [ ] Create `test-audio-detection.sh` for manual testing

### Step 2: Backup Current Configuration
```bash
# On the Pi
cp ~/start-surge-cli.sh ~/start-surge-cli.sh.backup
cp /etc/systemd/system/surge-xt-cli.service /etc/systemd/system/surge-xt-cli.service.backup
```

### Step 3: Deploy New Scripts
- [ ] Create `scripts/` directory if needed: `mkdir -p ~/scripts`
- [ ] Upload `detect-audio-device.sh` to `~/scripts/`
- [ ] Set executable: `chmod +x ~/scripts/detect-audio-device.sh`
- [ ] Upload new `start-surge-cli.sh` to `~/scripts/`
- [ ] Set executable: `chmod +x ~/scripts/start-surge-cli.sh`
- [ ] Update symlink: `ln -sf ~/scripts/start-surge-cli.sh ~/start-surge-cli.sh`

### Step 4: Update Service Configuration
```bash
# Update service file
sudo cp config/surge-xt-cli.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### Step 5: Testing on Pi
- [ ] Run manual detection test: `~/scripts/detect-audio-device.sh`
- [ ] Verify correct device selected
- [ ] Test service restart: `sudo systemctl restart surge-xt-cli`
- [ ] Check logs: `sudo journalctl -u surge-xt-cli -n 50`
- [ ] Test audio output: Play MIDI to verify sound

### Step 6: Scenario Testing
- [ ] Test with USB DAC connected
- [ ] Test with USB DAC disconnected (should use headphone jack)
- [ ] Test with different USB DAC
- [ ] Reboot test (cold start verification)

### Step 7: Documentation Update
- [ ] Update `CURRENT_STATE.md` with new audio detection approach
- [ ] Update `PROJECT_PLAN.md` to reflect robustness improvements
- [ ] Document new scripts in `README.md`

---

## Success Criteria

✅ **Must Have**:
- System boots and produces audio with USB DAC connected
- System boots and produces audio with NO USB DAC (uses headphone jack)
- Correct device logged in `surge-cli.log` with tier information
- No crashes related to audio device selection
- Systemd service remains stable after audio changes

✅ **Nice to Have**:
- Hot-plug USB DAC support (udev rules to restart service)
- Pre-launch device validation
- Retry logic if first device fails

---

## Future Enhancements

### Hot-Plug USB Audio Support
Create udev rule to restart Surge when USB audio devices connect/disconnect:

**File**: `config/99-usb-audio.rules`
```bash
# Restart Surge XT CLI when USB audio devices connect/disconnect
ACTION=="add", SUBSYSTEM=="sound", RUN+="/bin/systemctl restart surge-xt-cli.service"
ACTION=="remove", SUBSYSTEM=="sound", RUN+="/bin/systemctl restart surge-xt-cli.service"
```

Install with:
```bash
sudo cp config/99-usb-audio.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

### Audio Device Health Check
Add periodic checking of audio device status:
- Monitor for ALSA errors
- Restart service if audio output fails
- Log audio dropout events

### User-Configurable Device Priority
Allow user to specify preferred devices in config file:

**File**: `~/.surge-audio-priority`
```
1. Sound Blaster Play! 3
2. FiiO USB DAC
3. Headphones
4. *
```

---

## Risk Mitigation

### Risk 1: Detection script fails completely
- **Mitigation**: Service will fail to start, systemd will retry
- **Fallback**: Restore backup script, use hardcoded device

### Risk 2: Headphone jack has poor audio quality
- **Mitigation**: User can ensure USB DAC is always connected
- **Acceptance**: Better to have poor quality audio than crash

### Risk 3: Increased startup time
- **Mitigation**: Detection adds ~1-2 seconds max
- **Acceptance**: 2 extra seconds acceptable for robustness

### Risk 4: Systemd restart loops
- **Mitigation**: `StartLimitBurst` prevents infinite loops
- **Logging**: All failures logged for debugging

---

## Files to Create/Modify

### New Files
- ✅ `AUDIO_ROBUSTNESS_PLAN.md` (this document)
- [ ] `scripts/detect-audio-device.sh` (main detection logic)
- [ ] `scripts/test-audio-detection.sh` (testing tool)
- [ ] `config/99-usb-audio.rules` (optional: hot-plug support)

### Modified Files
- [ ] `scripts/start-surge-cli.sh` (use new detection script)
- [ ] `config/surge-xt-cli.service` (improved restart handling)
- [ ] `CURRENT_STATE.md` (document new audio approach)
- [ ] `PROJECT_PLAN.md` (update Phase 1 status)

### Backup Files (create before modifying)
- [ ] `scripts/start-surge-cli.sh.backup`
- [ ] `config/surge-xt-cli.service.backup`

---

## Timeline Estimate

**Phase 1-2**: 1-2 hours (script development)
**Phase 3**: 30 minutes (validation logic) - OPTIONAL
**Phase 4-5**: 30 minutes (service config, diagnostics)
**Testing & Deployment**: 1 hour

**Total**: ~3-4 hours for complete implementation and testing

---

## Questions for User

1. **Raspberry Pi headphone jack audio quality**: Is lower quality acceptable as fallback, or should we only use USB audio?

2. **Hot-plug support**: Do you want automatic restart when USB DACs are plugged/unplugged during operation, or is reboot acceptable?

3. **Pre-launch validation**: Should we add device testing before launch (adds ~1-2 seconds startup time)?

4. **ALSA vs surge device numbering**: Should we also try ALSA device names (hw:X,Y) as fallback if surge numbering fails?

---

**Status**: ⏳ Awaiting approval to implement
**Next Step**: Create `detect-audio-device.sh` and test with current Pi setup
