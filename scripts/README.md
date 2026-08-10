# Scripts Directory

## Active Production Scripts

### Core System Scripts
- **start-surge-cli.sh** - Primary Surge XT CLI startup with tiered audio detection
- **detect-audio-device.sh** - Audio fallback (gadget → USB DAC → Pi headphone → any device)
- **setup-usb-audio-gadget.sh** - Configfs UAC2 gadget for `MPE_AUDIO_PROFILE=usb-host`
- **surge-watchdog.sh** - Automatic crash recovery and service restart
- **start-patch-browser.sh** - Launch patch browser UI service
- **start-touch-patch-browser.sh** - Launch SmartiPi touch patch browser (pygame + KMS)

### Deployment Scripts
- **deploy-all.sh** - Complete system deployment (all services and scripts)
- **deploy-patches.sh** - Deploy custom user patches only (~5–10 sec)
- **deploy-patch-browser.sh** - Deploy UI updates only
- **deploy-boot-animation.sh** - Deploy boot animation updates
- **deploy-crash-fixes.sh** - Deploy crash fix scripts

### Backup & Sync Scripts
- **pull-all-from-device.sh** - Full backup from Pi to development machine
- **sync-from-device.sh** - Incremental backup from Pi

### Diagnostic Scripts
- **diagnose-pi-state.sh** - Complete system diagnostics (services, permissions, processes)
- **test-audio-detection.sh** - Test 4-tier audio fallback system
- **check-surge-mode.sh** - Verify CLI/GUI mode state

### Capture Scripts
- **record-screen.sh** - Record touch UI via in-app RGB pipe to ffmpeg (see [`docs/TOUCH_PATCH_BROWSER.md`](../docs/TOUCH_PATCH_BROWSER.md#screen-recording-for-demos))
- **build-patch-metadata-baseline.py** - Regenerate `data/patch_metadata_baseline.json` from local Surge patch dirs (auto-uses sibling `../MPE-Library` when present; else `~/surge` paths)

### Setup Scripts
- **setup-power-button.sh** - Configure GPIO power button (8-second hold to shutdown)
- **setup-touch-pi.sh** - SmartiPi touch Pi: apt deps, udev rules, `MPE_UI_MODE=touch` services
- **setup-usb-audio-gadget.sh** - UAC2 USB audio gadget bind/unbind (desk tether profile)

---

## Critical File Permission Requirements

### SurgeXTUserDefaults.xml MUST be chmod 644

**Location**: `~/.local/share/Surge XT/SurgeXTUserDefaults.xml`

**Why**: OSC `/patch/load` commands require Surge XT to write to this file. If set to read-only (chmod 444), Surge will crash with SEGV when loading complex patches.

**Scripts that manage this file**:
- `start-surge-cli.sh` - Creates minimal XML if missing, sets 644
- `surge-watchdog.sh` - After crash recovery, sets 644

**DO NOT**:
- Create services that set this file to 444 (read-only)
- Use chmod 444 in any script that runs in CLI mode
- Delete this file without recreating it as valid XML

---

## Important Notes

### VNC NOT SUPPORTED
The Raspberry Pi in headless mode cannot run VNC without a compositor. GUI editing is not supported remotely.

### Patch Editing Workflow
1. Edit patches using Surge XT GUI on **Windows**
2. Deploy patches to Pi using deployment scripts
3. Use hardware patch browser for live performance

### Mode Switching
**CLI-only mode** is the production configuration. There are no mode switcher scripts - the system boots directly into CLI mode via systemd services.

For emergency GUI access (requires HDMI monitor):
1. Stop services: `sudo systemctl stop patch-browser surge-xt-cli surge-watchdog`
2. Connect HDMI monitor
3. Start Surge XT GUI manually

### Audio Device Detection
The 4-tier fallback system automatically selects:
1. **Tier 1**: USB DAC (e.g., "USB Audio Device")
2. **Tier 2**: Pi headphone jack ("Headphones" or "bcm2835 Headphones")
3. **Tier 3**: Any "Front output" device
4. **Tier 4**: First available audio device

Logs are written to `/home/mitch/surge-cli.log`

---

## Archived Scripts

Legacy and development scripts have been moved to `../archive/`:
- `archive/development-tools/` - One-time analysis and modification tools
- `archive/setup-tools/` - One-time installation scripts
- `archive/legacy-scripts/` - Superseded scripts (old mode switchers, old deployment)

See `../archive/README.md` for details.
