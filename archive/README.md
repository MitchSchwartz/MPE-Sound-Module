# Archive Directory

This directory contains scripts and tools that are no longer part of the active production system but are preserved for reference.

## Directory Structure

### development-tools/
One-time development and analysis scripts used during initial development:
- `analyze_fxp.py` - FXP file format analysis
- `find_filter_params.py` - Find filter parameters in patches
- `extract_timbre_targets.py` - Extract timbre modulation targets
- `add_mpe_timbre_modulation.py` - Batch add timbre modulation to patches (completed)
- `test_add_timbre_mod.py` - Test timbre modulation addition
- `check_patch_complexity.py` - Analyze patch complexity metrics
- `encoder_controller.py` - Old 5-encoder controller (superseded by patch browser)

### setup-tools/
One-time installation and configuration scripts (already executed):
- `boot_config.sh` - Initial boot optimization configuration
- `install.sh` - Initial system installation
- `install_patch_browser.sh` - Initial patch browser installation

### legacy-scripts/
Superseded scripts replaced by improved versions:
- `deploy.sh` - Old deployment script (replaced by `deploy-all.sh`)
- `switch-to-gui.sh` - Old GUI mode switch (replaced by improved version, then removed)
- `switch-to-cli.sh` - Old CLI mode switch (replaced by improved version, then removed)
- `switch-to-gui-improved.sh` - GUI mode switcher (removed - VNC not supported)
- `switch-to-cli-improved.sh` - CLI mode switcher (removed - system now CLI-only)
- `launch-gui-vnc.sh` - VNC launcher (removed - VNC not supported on headless Pi)
- `enable-gui.sh` - Old GUI enablement requiring reboot (superseded)
- `disable-gui.sh` - Old GUI disablement requiring reboot (superseded)

## Why These Were Archived

### Development Tools
These scripts were used during initial development to analyze and modify patches. They served their purpose and are no longer needed for day-to-day operation.

### Setup Tools
These scripts configure the system during initial installation. They only need to be run once and are preserved for reference if the system needs to be rebuilt.

### Legacy Scripts
These scripts were replaced by improved versions or became obsolete when the project shifted to CLI-only mode:

- **Mode switchers removed**: The system now boots directly into CLI mode via systemd services. There's no need to switch between GUI and CLI modes.
- **VNC support removed**: VNC doesn't work on headless Raspberry Pi without a compositor. GUI editing happens on Windows, not via VNC.
- **Old deployment scripts**: Replaced by more robust deployment scripts with better error handling.

## Important Notes

**DO NOT** use scripts from this archive in production. They may:
- Set incorrect file permissions (chmod 444 breaks OSC)
- Conflict with current systemd service configuration
- Attempt to start VNC services that don't work
- Use outdated deployment workflows

If you need functionality from an archived script, review the current production scripts in `../scripts/` first - the feature may already exist in an improved form.
