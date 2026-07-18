#!/bin/bash
# Removed set -e so script can continue after non-critical errors

echo "=== Pi-Surge-MPE Installation Script ==="
echo ""

# Check if running on Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warning: This doesn't appear to be a Raspberry Pi"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "Step 1: Installing system dependencies..."
echo "Checking which packages need installation..."
sudo apt update
sudo apt install -y \
    jackd2 \
    qjackctl \
    a2jmidid \
    git \
    build-essential \
    cmake \
    libcairo2-dev \
    libxkbcommon-x11-dev \
    libxkbcommon-dev \
    libxcb-cursor-dev \
    libxcb-keysyms1-dev \
    libxcb-util-dev \
    libxrandr-dev \
    libxinerama-dev \
    libxcursor-dev \
    libasound2-dev \
    libjack-jackd2-dev \
    libfreetype6-dev \
    libglu1-mesa-dev \
    python3-pip \
    python3-gpiozero \
    python3-rtmidi

echo ""
echo "Step 2: Python dependencies already installed via apt (python3-gpiozero, python3-rtmidi)"

echo ""
echo "Step 3: Checking for Surge XT..."
if command -v Surge-XT &> /dev/null; then
    echo "Surge XT already installed: $(which Surge-XT)"
else
    echo "Surge XT not found. Installation required."
    echo ""
    echo "Please download the ARM64 build from:"
    echo "https://github.com/surge-synthesizer/releases-xt/releases"
    echo ""
    echo "Extract and install to /usr/local/bin/Surge-XT"
    echo "Or build from source: https://github.com/surge-synthesizer/surge"
    echo ""
    read -p "Have you installed Surge XT manually? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Please install Surge XT first, then re-run this script."
        exit 1
    fi
fi

echo ""
echo "Step 4: Configuring JACK..."
mkdir -p ~/.config/jack

# Create default .jackdrc if it doesn't exist
if [ ! -f ~/.jackdrc ]; then
    echo "Creating ~/.jackdrc..."
    cat > ~/.jackdrc << 'EOF'
/usr/bin/jackd -dalsa -dhw:1 -r48000 -p512 -n3
EOF
    echo "Created ~/.jackdrc (you'll need to update the device number)"
else
    echo "~/.jackdrc already exists, skipping"
fi

# Allow JACK to use realtime priority
if ! groups | grep -q audio; then
    echo "Adding user to 'audio' group for realtime priority..."
    sudo usermod -a -G audio $USER
    echo "You'll need to log out and back in for this to take effect"
fi

# Configure realtime limits
if [ ! -f /etc/security/limits.d/audio.conf ]; then
    echo "Configuring realtime audio limits..."
    sudo tee /etc/security/limits.d/audio.conf > /dev/null << 'EOF'
@audio   -  rtprio     95
@audio   -  memlock    unlimited
EOF
fi

echo ""
echo "Step 5: Setting up systemd services..."
mkdir -p ~/.config/systemd/user

echo "Creating systemd service files (will overwrite if they exist)..."

# JACK service
cat > ~/.config/systemd/user/jack.service << 'EOF'
[Unit]
Description=JACK Audio Server
After=sound.target

[Service]
Type=simple
ExecStart=/bin/bash -c 'exec $(/bin/cat ~/.jackdrc)'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Surge XT service
cat > ~/.config/systemd/user/surge.service << 'EOF'
[Unit]
Description=Surge XT Synthesizer
After=jack.service
Requires=jack.service

[Service]
Type=simple
ExecStartPre=/bin/sleep 3
ExecStart=/usr/local/bin/Surge-XT
Restart=on-failure
RestartSec=5
Environment=JACK_DEFAULT_SERVER=default

[Install]
WantedBy=default.target
EOF

# Encoder controller service
cat > ~/.config/systemd/user/encoders.service << 'EOF'
[Unit]
Description=Rotary Encoder Controller
After=surge.service
Requires=surge.service

[Service]
Type=simple
WorkingDirectory=%h/pisurge
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/python3 %h/pisurge/encoder_controller.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Reload systemd
systemctl --user daemon-reload

echo ""
echo "Step 6: Creating helper scripts..."

# Script to check audio devices
cat > ~/pisurge/check_audio.sh << 'EOF'
#!/bin/bash
echo "=== Audio Cards ==="
cat /proc/asound/cards
echo ""
echo "=== Playback Devices ==="
aplay -l
echo ""
echo "=== JACK Configuration ==="
cat ~/.jackdrc
EOF
chmod +x ~/pisurge/check_audio.sh

# Script to check MIDI devices
cat > ~/pisurge/check_midi.sh << 'EOF'
#!/bin/bash
echo "=== ALSA MIDI Devices ==="
aconnect -l
echo ""
echo "=== Raw MIDI Devices ==="
ls -la /dev/snd/midi* 2>/dev/null || echo "No /dev/snd/midi* devices found"
ls -la /dev/midi* 2>/dev/null || echo "No /dev/midi* devices found"
EOF
chmod +x ~/pisurge/check_midi.sh

# Script to monitor services
cat > ~/pisurge/monitor.sh << 'EOF'
#!/bin/bash
echo "=== Service Status ==="
systemctl --user status jack.service --no-pager
echo ""
systemctl --user status surge.service --no-pager
echo ""
systemctl --user status encoders.service --no-pager
echo ""
echo "=== JACK Connections ==="
jack_lsp -c
EOF
chmod +x ~/pisurge/monitor.sh

# Script to backup Surge config
cat > ~/pisurge/backup_surge.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=~/surge-backups
mkdir -p $BACKUP_DIR
BACKUP_FILE=$BACKUP_DIR/surge-backup-$(date +%Y%m%d-%H%M%S).tar.gz

echo "Backing up Surge XT configuration..."
tar czf $BACKUP_FILE \
  ~/.config/surge-xt \
  ~/.local/share/surge-xt \
  2>/dev/null

if [ $? -eq 0 ]; then
    echo "✓ Backup saved to: $BACKUP_FILE"
    ls -lh $BACKUP_FILE
else
    echo "✗ Backup failed (Surge may not be configured yet)"
fi
EOF
chmod +x ~/pisurge/backup_surge.sh

# Script to check Surge version
cat > ~/pisurge/surge_version.sh << 'EOF'
#!/bin/bash
echo "=== Surge XT Version Info ==="

if [ -d ~/surge/.git ]; then
    echo "Git commit: $(git -C ~/surge rev-parse --short HEAD 2>/dev/null)"
    echo "Git date: $(git -C ~/surge log -1 --format=%cd 2>/dev/null)"
    echo "Git branch: $(git -C ~/surge branch --show-current 2>/dev/null)"
fi

if [ -f /usr/local/bin/Surge-XT ]; then
    echo ""
    echo "Binary: /usr/local/bin/Surge-XT"
    echo "Size: $(ls -lh /usr/local/bin/Surge-XT | awk '{print $5}')"
    echo "Modified: $(stat -c %y /usr/local/bin/Surge-XT 2>/dev/null)"
else
    echo ""
    echo "Surge-XT binary not found at /usr/local/bin/Surge-XT"
fi
EOF
chmod +x ~/pisurge/surge_version.sh

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "1. Run ~/pisurge/check_audio.sh to find your Sound Blaster S3 card number"
echo "2. Edit ~/.jackdrc and update 'hw:X' with the correct card number"
echo "3. Test JACK manually: jackd -dalsa -dhw:X -r48000 -p512 -n3"
echo "4. Test Surge XT: systemctl --user start jack.service && Surge-XT"
echo "5. Configure MPE in Surge XT (Menu > MPE Settings > Enable)"
echo "6. Connect Roli controller and test MPE input"
echo "7. Wire encoders according to INSTALL.md"
echo "8. Enable auto-start: systemctl --user enable jack.service surge.service"
echo ""
echo "Helpful commands:"
echo "  ~/pisurge/check_audio.sh   - Check audio devices"
echo "  ~/pisurge/check_midi.sh    - Check MIDI devices"
echo "  ~/pisurge/monitor.sh       - Monitor service status"
echo "  ~/pisurge/backup_surge.sh  - Backup Surge config & presets"
echo "  ~/pisurge/surge_version.sh - Check Surge version info"
echo ""
echo "If you were added to the 'audio' group, log out and back in."
