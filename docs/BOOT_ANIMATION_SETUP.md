# Boot Animation Setup Guide

This guide explains how to set up the boot animation on the 1.3" OLED display.

## What It Does

- Shows a loading animation on the OLED display during system boot
- Provides visual feedback that the system is starting up
- Automatically stops when the patch browser starts
- Displays "Pi-Surge-MPE" title with a rotating spinner and status messages

## Files Created

### Scripts
- `boot_animation.py` - Boot animation display script
- `scripts/start-patch-browser.sh` - Wrapper script that stops boot animation and starts patch browser

### Systemd Services
- `config/boot-animation.service` - Runs boot animation on startup
- `config/patch-browser.service` - Runs patch browser UI (stops boot animation when starting)

## Installation Steps

### 1. Copy Files to Raspberry Pi

```bash
# From your development machine
scp boot_animation.py mitch@surge.local:~/
scp scripts/start-patch-browser.sh mitch@surge.local:~/
scp config/boot-animation.service mitch@surge.local:~/
scp config/patch-browser.service mitch@surge.local:~/
```

### 2. Make Scripts Executable

```bash
# On the Raspberry Pi
ssh mitch@surge.local
chmod +x ~/boot_animation.py
chmod +x ~/start-patch-browser.sh
```

### 3. Test Boot Animation (Optional)

Test the animation before installing the service:

```bash
# Run for 10 seconds with test mode
python3 ~/boot_animation.py --test

# Or run indefinitely (Ctrl+C to stop)
python3 ~/boot_animation.py
```

### 4. Install Systemd Services

```bash
# Copy service files to systemd directory
sudo cp ~/boot-animation.service /etc/systemd/system/
sudo cp ~/patch-browser.service /etc/systemd/system/

# Reload systemd to recognize new services
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable boot-animation.service
sudo systemctl enable patch-browser.service
```

### 5. Configure Service Order

The services are already configured to start in the correct order:

1. **boot-animation.service** - Starts early during boot
2. **surge-xt-cli.service** - Starts Surge (existing service)
3. **patch-browser.service** - Stops boot animation, starts patch browser

The `Conflicts=` directive ensures the boot animation stops when the patch browser starts.

### 6. Reboot to Test

```bash
sudo reboot
```

## Expected Behavior

1. **System boots** - Boot animation starts immediately
2. **Animation runs** - Shows rotating spinner with status messages
3. **Surge starts** - Boot animation continues running
4. **Patch browser starts** - Boot animation stops, patch browser takes over display

## Troubleshooting

### Check Service Status

```bash
# Check if boot animation is running
sudo systemctl status boot-animation.service

# Check if patch browser is running
sudo systemctl status patch-browser.service

# View logs
sudo journalctl -u boot-animation.service -f
sudo journalctl -u patch-browser.service -f
```

### Boot Animation Won't Start

1. Check I2C is enabled: `sudo raspi-config` → Interface Options → I2C
2. Verify display connection: `sudo i2cdetect -y 1` (should show device at 0x3C)
3. Check service logs: `sudo journalctl -u boot-animation.service`

### Animation Won't Stop

If the boot animation doesn't stop when the patch browser starts:

```bash
# Manually stop it
sudo systemctl stop boot-animation.service

# Check service conflicts
sudo systemctl show boot-animation.service | grep Conflicts
sudo systemctl show patch-browser.service | grep Conflicts
```

### Display Shows Nothing

1. Verify OLED is connected properly (I2C pins: GPIO 2/3)
2. Check if luma.oled is installed: `pip3 list | grep luma`
3. Test display directly: `python3 ~/boot_animation.py --test`

## Customization

### Change Animation Duration

Edit `boot-animation.service` and add a duration parameter:

```ini
ExecStart=/usr/bin/python3 /home/mitch/boot_animation.py --duration 15
```

This will run the animation for exactly 15 seconds then exit.

### Modify Animation

Edit `boot_animation.py` to customize:

- **Status messages** (line ~90): Change the text displayed
- **Animation speed** (line ~31): Adjust `ANIMATION_FPS`
- **Spinner design** (line ~60): Modify `draw_spinner()` method

### Change Font

The script uses DejaVu Sans Bold. To use a different font, edit line ~50:

```python
self.font = ImageFont.truetype("/path/to/your/font.ttf", 16)
```

## Uninstalling

To remove the boot animation:

```bash
# Disable and stop services
sudo systemctl stop boot-animation.service patch-browser.service
sudo systemctl disable boot-animation.service patch-browser.service

# Remove service files
sudo rm /etc/systemd/system/boot-animation.service
sudo rm /etc/systemd/system/patch-browser.service

# Reload systemd
sudo systemctl daemon-reload

# Remove scripts (optional)
rm ~/boot_animation.py
rm ~/start-patch-browser.sh
```

## Notes

- The boot animation uses minimal CPU (~1-2%)
- Animation runs at 10 FPS by default for smooth motion
- Display is cleared automatically when animation stops
- The patch browser can still run standalone without the boot animation service
