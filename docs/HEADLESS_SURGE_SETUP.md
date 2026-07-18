# Headless Surge XT Setup Guide

## Architecture Overview

```
┌─────────────────────────┐
│  Custom Python App      │
│  - Read presets         │
│  - GPIO encoder input   │
│  - Send MIDI to Surge   │
│  - Display on 1.3" OLED │
└───────────┬─────────────┘
            │ MIDI
            ▼
┌─────────────────────────┐
│  Surge XT (headless)    │
│  - ALSA audio backend   │
│  - MIDI from Roli       │
│  - MIDI from Python app │
└─────────────────────────┘
            │
            ▼
      Audio Output
```

## Phase 1: Headless Surge with ALSA

### Step 1: Configure Surge XT to use ALSA backend

Create the Surge XT startup script:

```bash
#!/bin/bash
# /home/pi/start-surge-headless.sh

# Set audio backend to ALSA
export SURGE_AUDIO_BACKEND=alsa

# Start Surge XT standalone (requires X11 but minimal overhead)
cd /home/pi/surge/build/surge-xt-distribution
./Surge\ XT &

echo "Surge XT running headless on ALSA"
echo "PID: $!"
```

Make it executable:
```bash
chmod +x /home/pi/start-surge-headless.sh
```

### Step 2: Install VNC Server

We'll use **x11vnc** (lightweight, works with existing X session):

```bash
sudo apt-get update
sudo apt-get install -y x11vnc
```

Set VNC password:
```bash
x11vnc -storepasswd /home/pi/.vnc/passwd
```

### Step 3: Create VNC startup script

```bash
#!/bin/bash
# /home/pi/start-vnc.sh

# Start X11 if not already running
if ! pgrep -x "Xorg" > /dev/null; then
    startx &
    sleep 3
fi

# Start x11vnc server
x11vnc -display :0 \
       -rfbauth /home/pi/.vnc/passwd \
       -rfbport 5900 \
       -forever \
       -shared \
       -bg \
       -o /home/pi/vnc.log

echo "VNC server started on port 5900"
echo "Connect from Windows using: <Pi-IP-Address>:5900"
```

Make it executable:
```bash
chmod +x /home/pi/start-vnc.sh
```

### Step 4: Access from Windows

Install a VNC client on Windows:
- **RealVNC Viewer** (recommended)
- **TightVNC Viewer**
- **TigerVNC**

Connect to: `<raspberry-pi-ip>:5900`

## Phase 2: Custom Display Controller

### Hardware Requirements

**Option A: SSD1306 OLED (128x64, I2C)**
- Cheap (~$5)
- Monochrome
- Low power
- Easy I2C connection

**Option B: ST7789 LCD (240x240, SPI)**
- Color display
- Higher resolution
- More expensive (~$15)

### Enable I2C (for SSD1306)

```bash
sudo raspi-config
# Interface Options → I2C → Enable
```

### Install Python Libraries

```bash
pip3 install luma.oled pillow python-rtmidi RPi.GPIO
```

### Preset Browser Structure

The Python app will:

1. **Scan Surge presets** from `~/.local/share/surge-xt/`
2. **Listen to rotary encoder** GPIO pins
3. **Display current category/patch** on OLED
4. **Send MIDI Program Change** to Surge when patch selected

## Phase 3: Integration

### System Services

Create systemd service for auto-start:

```ini
# /etc/systemd/system/surge-headless.service
[Unit]
Description=Surge XT Headless Synth
After=network.target sound.target

[Service]
Type=simple
User=pi
ExecStart=/home/pi/start-surge-headless.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable:
```bash
sudo systemctl enable surge-headless.service
sudo systemctl start surge-headless.service
```

## Usage Workflow

### For Performance (No GUI)
1. Power on Pi
2. Surge XT starts automatically in background
3. Custom Python display controller shows presets
4. Use encoders to navigate and select patches
5. Play via Roli MPE controller

### For Configuration (GUI via VNC)
1. SSH into Pi: `ssh pi@<ip>`
2. Run: `./start-vnc.sh`
3. Connect from Windows VNC client
4. Configure Surge XT parameters, effects, modulation
5. Save presets
6. Disconnect VNC when done

## Next Steps

1. Test headless Surge startup
2. Configure VNC access from Windows
3. Map Surge preset directory structure
4. Design preset browser UI for 1.3" display
5. Wire up rotary encoders to GPIO
6. Build Python controller application
