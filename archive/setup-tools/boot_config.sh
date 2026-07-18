#!/bin/bash
set -e

echo "=== Pi-Surge-MPE Boot Optimization ==="
echo ""
echo "This script will disable unnecessary services to improve boot time."
echo "Target: < 30 seconds to audio-ready state"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi

echo ""
echo "Disabling unnecessary services..."

# Disable services that aren't needed for headless audio
DISABLE_SERVICES=(
    "bluetooth.service"
    "hciuart.service"
    "triggerhappy.service"
    "avahi-daemon.service"
    "ModemManager.service"
    "wpa_supplicant.service"  # Only if using Ethernet
)

for service in "${DISABLE_SERVICES[@]}"; do
    if systemctl is-enabled "$service" &>/dev/null; then
        echo "Disabling $service..."
        sudo systemctl disable "$service" || true
    fi
done

echo ""
echo "Configuring kernel boot parameters..."

# Edit /boot/cmdline.txt for faster boot
CMDLINE="/boot/cmdline.txt"
if [ -f "$CMDLINE" ]; then
    sudo cp "$CMDLINE" "${CMDLINE}.backup"

    # Remove quiet and splash, add performance parameters
    CURRENT=$(cat "$CMDLINE")

    # Add threadirqs for better realtime performance
    if ! echo "$CURRENT" | grep -q "threadirqs"; then
        echo "Adding threadirqs to kernel parameters..."
        echo "$CURRENT threadirqs" | sudo tee "$CMDLINE" > /dev/null
    fi

    # Add dwc_otg.fiq_fsm_enable=0 for USB audio reliability
    if ! echo "$CURRENT" | grep -q "dwc_otg.fiq_fsm_enable"; then
        CURRENT=$(cat "$CMDLINE")
        echo "$CURRENT dwc_otg.fiq_fsm_enable=0" | sudo tee "$CMDLINE" > /dev/null
    fi
fi

echo ""
echo "Configuring /boot/config.txt..."

CONFIG="/boot/config.txt"
if [ -f "$CONFIG" ]; then
    sudo cp "$CONFIG" "${CONFIG}.backup"

    # Add performance settings
    if ! grep -q "force_turbo=1" "$CONFIG"; then
        echo "" | sudo tee -a "$CONFIG" > /dev/null
        echo "# Performance settings for Pi-Surge-MPE" | sudo tee -a "$CONFIG" > /dev/null
        echo "force_turbo=1" | sudo tee -a "$CONFIG" > /dev/null
        echo "over_voltage=2" | sudo tee -a "$CONFIG" > /dev/null
    fi

    # Disable HDMI if using headless
    if ! grep -q "hdmi_blanking=2" "$CONFIG"; then
        echo "hdmi_blanking=2" | sudo tee -a "$CONFIG" > /dev/null
    fi

    # Disable WiFi/Bluetooth if not needed (comment out if you need WiFi)
    # echo "dtoverlay=disable-wifi" | sudo tee -a "$CONFIG" > /dev/null
    # echo "dtoverlay=disable-bt" | sudo tee -a "$CONFIG" > /dev/null
fi

echo ""
echo "Setting CPU governor to performance..."

# Install cpufrequtils if not present
if ! command -v cpufreq-set &> /dev/null; then
    sudo apt install -y cpufrequtils
fi

# Create service to set performance governor on boot
cat > /tmp/cpu-performance.service << 'EOF'
[Unit]
Description=Set CPU governor to performance
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/bin/cpufreq-set -g performance -c 0
ExecStart=/usr/bin/cpufreq-set -g performance -c 1
ExecStart=/usr/bin/cpufreq-set -g performance -c 2
ExecStart=/usr/bin/cpufreq-set -g performance -c 3
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo mv /tmp/cpu-performance.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cpu-performance.service

echo ""
echo "Optimizing system limits..."

# Increase file descriptors for JACK
if ! grep -q "fs.file-max" /etc/sysctl.conf; then
    echo "fs.file-max = 100000" | sudo tee -a /etc/sysctl.conf > /dev/null
fi

# Reduce swappiness
if ! grep -q "vm.swappiness" /etc/sysctl.conf; then
    echo "vm.swappiness = 10" | sudo tee -a /etc/sysctl.conf > /dev/null
fi

echo ""
echo "Creating boot time measurement script..."

cat > ~/pisurge/measure_boot_time.sh << 'EOF'
#!/bin/bash
echo "=== Boot Time Analysis ==="
systemd-analyze
echo ""
echo "=== Service Timing ==="
systemd-analyze blame | head -20
echo ""
echo "=== Critical Chain ==="
systemd-analyze critical-chain
echo ""
echo "=== User Service Timing ==="
systemd-analyze --user blame | head -10
EOF
chmod +x ~/pisurge/measure_boot_time.sh

echo ""
echo "=== Boot Optimization Complete ==="
echo ""
echo "Changes made:"
echo "  - Disabled unnecessary services"
echo "  - Added kernel parameters for realtime performance"
echo "  - Set CPU governor to performance"
echo "  - Optimized system limits"
echo ""
echo "Backups created:"
echo "  - /boot/cmdline.txt.backup"
echo "  - /boot/config.txt.backup"
echo ""
echo "IMPORTANT: You must reboot for changes to take effect."
echo ""
echo "After reboot, run: ~/pisurge/measure_boot_time.sh"
echo "to verify boot time is under 30 seconds."
echo ""
read -p "Reboot now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
fi
