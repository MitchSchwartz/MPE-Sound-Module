#!/bin/bash
# Fix Surge XT GUI/CLI Crashes Caused by Corrupted User Defaults
#
# This script implements multiple fixes for the SurgeXTUserDefaults.xml
# corruption issue that causes random crashes when loading patches.

set -e

USER_DEFAULTS_DIR="$HOME/.local/share/Surge XT"
USER_DEFAULTS_FILE="$USER_DEFAULTS_DIR/SurgeXTUserDefaults.xml"
SURGE_SERVICE="surge-xt-cli.service"

echo "=== Surge Crash Fix Script ==="
echo ""

# Check if running on Pi or local dev machine
if [[ $(hostname) == "surge" ]] || [[ $(hostname) == *"raspberrypi"* ]]; then
    ON_PI=true
    echo "Running on Raspberry Pi"
else
    ON_PI=false
    echo "Running on development machine (deploy to Pi after testing)"
fi

echo ""

# Function to backup user defaults
backup_user_defaults() {
    if [ -f "$USER_DEFAULTS_FILE" ]; then
        local backup_file="${USER_DEFAULTS_FILE}.backup_$(date +%Y%m%d_%H%M%S)"
        echo "Backing up existing user defaults to: $backup_file"
        cp "$USER_DEFAULTS_FILE" "$backup_file"
    fi
}

# Function to check service status
check_service() {
    if ! $ON_PI; then
        echo "Skipping service check (not on Pi)"
        return
    fi

    echo "Checking Surge service status..."
    if systemctl is-active --quiet "$SURGE_SERVICE"; then
        echo "✓ Surge service is running"
    else
        echo "⚠ Surge service is NOT running"
        sudo systemctl status "$SURGE_SERVICE" --no-pager || true
    fi
}

echo "Choose a fix method:"
echo ""
echo "1. Clear corrupted file (immediate fix, may recur)"
echo "2. Ensure file is writable for OSC (REQUIRED - fixes permission crashes)"
echo "3. Symlink to /dev/null (NOT RECOMMENDED - breaks OSC)"
echo "4. Install watchdog service (auto-recovery on crash)"
echo "5. Options 1, 2, and 4 (recommended - skip option 3)"
echo ""
read -p "Enter choice (1-5): " choice

case $choice in
    1|5)
        echo ""
        echo "=== Option 1: Clearing Corrupted File ==="
        backup_user_defaults
        if [ -f "$USER_DEFAULTS_FILE" ]; then
            rm -f "$USER_DEFAULTS_FILE"
            echo "✓ Removed corrupted user defaults"
        else
            echo "✓ No user defaults file found (already clean)"
        fi

        if $ON_PI; then
            echo "Restarting Surge service..."
            sudo systemctl restart "$SURGE_SERVICE"
            sleep 2
            check_service
        fi

        if [ "$choice" != "5" ]; then
            echo ""
            echo "⚠ WARNING: This is a temporary fix. The file can get corrupted again."
            echo "   Consider using option 2, 3, or 4 for a permanent solution."
            exit 0
        fi
        ;;
esac

case $choice in
    2|5)
        echo ""
        echo "=== Option 2: Ensure File is Writable (Required for OSC) ==="

        # Ensure file exists first
        mkdir -p "$USER_DEFAULTS_DIR"
        if [ ! -f "$USER_DEFAULTS_FILE" ]; then
            echo "Creating minimal valid XML file..."
            cat > "$USER_DEFAULTS_FILE" << 'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<surge-xt-user-defaults>
</surge-xt-user-defaults>
XMLEOF
        fi

        # CRITICAL: Must be writable (644) for OSC patch loading
        # chmod 444 (read-only) causes Surge to crash with 6GB allocation failure
        chmod 644 "$USER_DEFAULTS_FILE"
        echo "✓ Set user defaults to writable (644) - REQUIRED for OSC patch loading"
        ls -l "$USER_DEFAULTS_FILE"

        if [ "$choice" != "5" ]; then
            echo ""
            echo "✓ Done. File is now writable, OSC patch loading will work correctly."
            exit 0
        fi
        ;;
esac

case $choice in
    3)
        echo ""
        echo "=== Option 3: Symlink to /dev/null (NOT RECOMMENDED) ==="
        echo ""
        echo "⚠️  WARNING: This option BREAKS OSC patch loading!"
        echo "    Symlink to /dev/null causes the same 6GB allocation crash as chmod 444."
        echo "    This option is kept for legacy compatibility only."
        echo ""
        read -p "Are you sure you want to continue? (yes/no): " confirm

        if [ "$confirm" != "yes" ]; then
            echo "Cancelled. Use option 2 instead for proper OSC support."
            exit 0
        fi

        # Remove existing file
        if [ -e "$USER_DEFAULTS_FILE" ] || [ -L "$USER_DEFAULTS_FILE" ]; then
            rm -f "$USER_DEFAULTS_FILE"
        fi

        # Create symlink
        ln -s /dev/null "$USER_DEFAULTS_FILE"
        echo "✓ Created symlink to /dev/null"
        ls -l "$USER_DEFAULTS_FILE"
        echo ""
        echo "⚠️  OSC patch loading will NOT work with this configuration!"
        exit 0
        ;;
esac

case $choice in
    4|5)
        echo ""
        echo "=== Option 4: Install Watchdog Service ==="

        if ! $ON_PI; then
            echo "⚠ Watchdog service can only be installed on the Pi"
            echo "   Deploy these files to the Pi:"
            echo "   - scripts/surge-watchdog.sh"
            echo "   - config/surge-watchdog.service"
            exit 0
        fi

        # Install watchdog script
        if [ ! -f "$HOME/scripts/surge-watchdog.sh" ]; then
            echo "Error: surge-watchdog.sh not found"
            echo "Please copy it to $HOME/scripts/surge-watchdog.sh first"
            exit 1
        fi

        chmod +x "$HOME/scripts/surge-watchdog.sh"

        # Install watchdog service
        if [ ! -f "$HOME/config/surge-watchdog.service" ]; then
            echo "Error: surge-watchdog.service not found"
            echo "Please copy it to $HOME/config/surge-watchdog.service first"
            exit 1
        fi

        sudo cp "$HOME/config/surge-watchdog.service" /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable surge-watchdog.service
        sudo systemctl start surge-watchdog.service

        echo "✓ Watchdog service installed and started"
        sudo systemctl status surge-watchdog.service --no-pager
        ;;
esac

echo ""
echo "=== Fix Complete ==="
echo ""

if $ON_PI; then
    check_service

    echo ""
    echo "Monitor Surge logs with:"
    echo "  tail -f ~/surge-cli.log"
    echo ""
    echo "Check service status with:"
    echo "  sudo systemctl status surge-xt-cli"
fi

echo ""
echo "✅ Surge crash fixes applied successfully!"
