# Power Button Setup Guide

This guide explains how to configure the encoder button for complete power management: shutdown when running, and power-on when off.

## Features

### When System is Running
- **Hold 8 seconds**: Display shows "RELEASE TO POWER OFF"
- **Release button**: System shuts down gracefully via `sudo poweroff`

### When System is Off
- **Hold 3 seconds**: System powers on (hardware wake)

## How It Works

### Software Layer (Running System)
The patch browser monitors button press duration:
1. Button pressed → Timer starts
2. After 8 seconds → Display shows "RELEASE TO POWER OFF"
3. Button released → System executes `sudo poweroff`

### Hardware Layer (Powered Off)
Raspberry Pi's `gpio-shutdown` device tree overlay:
1. Monitors GPIO 22 even when powered off (uses minimal power)
2. Button held 3+ seconds → Hardware power-on signal
3. Pi boots normally with boot animation

## Installation

### 1. Deploy Updated Patch Browser

The patch browser now includes visual feedback for poweroff:

```bash
# From development machine
bash scripts/deploy-patch-browser.sh
```

### 2. Fix Boot Animation Service

Deploy the corrected boot animation service:

```bash
# From development machine
bash scripts/deploy-boot-animation.sh
```

### 3. Configure Hardware Power Button

Run the power button setup script on the Pi:

```bash
# Copy script to Pi
scp scripts/setup-power-button.sh <pi-user>@surge.local:~/

# SSH to Pi and run setup
ssh <pi-user>@surge.local
chmod +x ~/setup-power-button.sh
./setup-power-button.sh
```

The script will:
- Backup `/boot/firmware/config.txt`
- Add `gpio-shutdown` device tree overlay
- Configure GPIO 22 as power button
- Offer to reboot

### 4. Test the Setup

After reboot, test both directions:

**Test Power Off:**
1. Hold encoder button for 8 seconds
2. Display should show "RELEASE TO POWER OFF"
3. Release button
4. System should shut down

**Test Power On:**
1. With system off, hold encoder button for 3+ seconds
2. Pi should power on and show boot animation
3. Patch browser should load normally

## Technical Details

### GPIO Configuration

```
dtoverlay=gpio-shutdown,gpio_pin=22,active_low=1,gpio_pull=up,debounce=3000
```

Parameters:
- `gpio_pin=22`: Uses encoder button (GPIO 22, Pin 15)
- `active_low=1`: Button press connects to ground (active low)
- `gpio_pull=up`: Internal pull-up resistor enabled
- `debounce=3000`: Must hold 3 seconds (prevents accidental power-on)

### Why Different Times?

- **3 seconds to power on**: Hardware limitation, minimum safe debounce
- **8 seconds to power off**: Software choice, prevents accidental shutdown during normal use

### Button Press Timeline

When powered ON:
```
0s ────→ 0.5s ────────────────→ 8s ────→ Release
  ignored   mode toggle           POWEROFF  → shutdown
            (aim ~1s)             warning
```

When powered OFF:
```
0s ────→ 3s ────→ Release
  waiting  POWER   → boot
           ON
```

## Troubleshooting

### Power Off Not Working

**Display doesn't show "RELEASE TO POWER OFF":**
- Check patch browser is running: `sudo systemctl status patch-browser.service`
- View logs: `sudo journalctl -u patch-browser.service -f`
- Verify OLED is working: Try mode change (~1s hold)

**System doesn't shutdown after release:**
- Check user has sudo permissions: `sudo poweroff` (manually test)
- Add to sudoers if needed: `sudo visudo` → add `<pi-user> ALL=(ALL) NOPASSWD: /sbin/poweroff`

### Power On Not Working

**Button hold doesn't power on Pi:**
- Verify config.txt has overlay: `grep gpio-shutdown /boot/firmware/config.txt`
- Check GPIO 22 wiring (should match patch browser button pin)
- Try 5+ second hold (some Pi models need longer)
- Ensure button connects GPIO 22 to ground (active low)

**Power on works but no boot animation:**
- Check service status: `sudo systemctl status boot-animation.service`
- View logs: `sudo journalctl -u boot-animation.service`
- Verify service is enabled: `sudo systemctl is-enabled boot-animation.service`

### Boot Animation Not Showing

If boot animation doesn't appear on startup:

```bash
# Check service is enabled
sudo systemctl is-enabled boot-animation.service

# If not enabled
sudo systemctl enable boot-animation.service

# View service definition
sudo systemctl cat boot-animation.service

# Test manually
python3 ~/boot_animation.py --test

# Redeploy if needed
bash scripts/deploy-boot-animation.sh
```

## Hardware Requirements

- Encoder button must be on GPIO 22 (Pin 15)
- Button should connect GPIO 22 to Ground when pressed
- Internal pull-up resistor is used (no external resistor needed)

## Uninstalling

### Remove Power Button Hardware Wake

```bash
# SSH to Pi
ssh <pi-user>@surge.local

# Remove gpio-shutdown overlay
sudo sed -i '/gpio-shutdown/d' /boot/firmware/config.txt

# Reboot
sudo reboot
```

Power off via button will still work (software), but power-on won't.

### Remove All Power Features

```bash
# Disable and remove services
sudo systemctl stop patch-browser.service boot-animation.service
sudo systemctl disable patch-browser.service boot-animation.service
sudo rm /etc/systemd/system/patch-browser.service
sudo rm /etc/systemd/system/boot-animation.service

# Remove hardware wake
sudo sed -i '/gpio-shutdown/d' /boot/firmware/config.txt

# Reboot
sudo reboot
```

## Safety Notes

- **3-second debounce prevents accidental power-on** during transport/storage
- **8-second poweroff requires intentional hold** to prevent accidental shutdown
- **Visual feedback confirms** when shutdown threshold is reached
- **Graceful shutdown** ensures proper filesystem sync before power off

## References

- [Raspberry Pi GPIO Shutdown Overlay](https://github.com/raspberrypi/linux/blob/rpi-6.1.y/arch/arm/boot/dts/overlays/gpio-shutdown-overlay.dts)
- [Device Tree Overlays Documentation](https://www.raspberrypi.com/documentation/computers/config_txt.html#part3)
