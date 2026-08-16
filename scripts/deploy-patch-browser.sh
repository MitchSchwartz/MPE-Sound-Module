#!/bin/bash
#
# Deploy Patch Browser UI to Raspberry Pi
# Deployment method: Push to GitHub, then pull from GitHub on Pi
#

set -e  # Exit on error

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
PI_REPO_PATH="$MPE_MODULE_REPO"
PI_REPO_REMOTE="${PI_MPE_MODULE:-\$HOME/MPE-Module}"

# Detect if we're running on the Pi itself
# Check if current directory is the repo path, or if we're in a git repo that matches
RUNNING_ON_PI=false
if [ -d "$PI_REPO_PATH/.git" ] && [ "$(pwd)" = "$PI_REPO_PATH" ]; then
    RUNNING_ON_PI=true
elif [ -d ".git" ] && [ "$(pwd)" = "$PI_REPO_PATH" ]; then
    RUNNING_ON_PI=true
elif [ "$(hostname)" = "surge" ] || [ "$(hostname)" = "raspberrypi" ] || hostname | grep -q "raspberrypi"; then
    RUNNING_ON_PI=true
fi

# Find SSH key - only needed if running from remote machine
if [ "$RUNNING_ON_PI" = false ]; then
    if [ -z "$SSH_KEY" ]; then
        # Try common locations
        if [ -f "$HOME/.ssh/surge_pi_key" ]; then
            SSH_KEY="$HOME/.ssh/surge_pi_key"
        elif [ -n "$USERPROFILE" ] && [ -f "$USERPROFILE/.ssh/surge_pi_key" ]; then
            # Windows path (works in Git Bash)
            SSH_KEY="$USERPROFILE/.ssh/surge_pi_key"
        elif [ -f "/c/Users/$USER/.ssh/surge_pi_key" ]; then
            SSH_KEY="/c/Users/$USER/.ssh/surge_pi_key"
        elif [ -f "$(cygpath -u "$USERPROFILE")/.ssh/surge_pi_key" ] 2>/dev/null; then
            SSH_KEY="$(cygpath -u "$USERPROFILE")/.ssh/surge_pi_key"
        elif [ -f "$HOME/.ssh/id_rsa" ]; then
            SSH_KEY="$HOME/.ssh/id_rsa"
        else
            SSH_KEY=""
        fi
    fi
fi

echo "========================================"
echo "Patch Browser UI Deployment"
echo "========================================"
echo ""

if [ "$RUNNING_ON_PI" = true ]; then
    echo "Running on Pi - will pull directly and restart service"
    echo "Repo path: $(pwd)"
else
    echo "Deployment method: Push to GitHub → Pull on Pi"
    echo "Target: ${PI_USER}@${PI_HOST}"
fi
echo ""

# Step 1: Check for uncommitted changes
echo "[1/5] Checking git status..."
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "⚠️  WARNING: You have uncommitted changes!"
    echo ""
    echo "Uncommitted changes:"
    git status --short
    echo ""
    read -p "Do you want to commit these changes now? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Staging all changes..."
        git add -A
        echo "Enter commit message (or press Enter for default):"
        read -r COMMIT_MSG
        if [ -z "$COMMIT_MSG" ]; then
            COMMIT_MSG="Update patch browser: reduce debounce for hardware capacitors"
        fi
        git commit -m "$COMMIT_MSG"
        echo "✓ Changes committed"
    else
        echo "❌ Please commit your changes first, then run this script again"
        exit 1
    fi
else
    echo "✓ No uncommitted changes"
fi
echo ""

# Step 2: Push to GitHub (if there are commits to push)
echo "[2/5] Checking if push to GitHub is needed..."
if git rev-parse --verify origin/main >/dev/null 2>&1 || git rev-parse --verify origin/master >/dev/null 2>&1; then
    # Check if local is ahead of remote
    LOCAL=$(git rev-parse @ 2>/dev/null)
    REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "")
    if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
        echo "  Local branch is ahead of remote - pushing to GitHub..."
        if git push; then
            echo "✓ Successfully pushed to GitHub"
        else
            echo "❌ ERROR: Failed to push to GitHub"
            echo "Please check your git remote and try again"
            exit 1
        fi
    else
        echo "✓ No commits to push (already up to date)"
    fi
else
    # No remote tracking branch, try to push anyway
    echo "  Pushing to GitHub..."
    if git push; then
        echo "✓ Successfully pushed to GitHub"
    else
        echo "⚠️  Warning: Push failed, but continuing with pull..."
    fi
fi
echo ""

if [ "$RUNNING_ON_PI" = true ]; then
    # Running on Pi - skip SSH, work directly
    REPO_DIR="$(pwd)"
    if [ ! -d "$REPO_DIR/.git" ]; then
        # Try the standard path
        if [ -d "$PI_REPO_PATH/.git" ]; then
            REPO_DIR="$PI_REPO_PATH"
        else
            echo "❌ ERROR: Not in a git repository"
            echo "Please run this script from the MPE-Module directory"
            exit 1
        fi
    fi
    
    # Step 3: Pull from GitHub (we're already on Pi)
    echo "[3/4] Pulling latest changes from GitHub..."
    cd "$REPO_DIR"
    
    # Check if repo exists
    if [ ! -d ".git" ]; then
        echo "❌ ERROR: Git repo not found at $REPO_DIR"
        exit 1
    fi
    
    # Pull latest changes
    echo "  - Fetching from GitHub..."
    git fetch origin
    
    echo "  - Pulling latest changes..."
    git pull origin main || git pull origin master
    
    if [ $? -ne 0 ]; then
        echo "❌ ERROR: Failed to pull from GitHub"
        exit 1
    fi
    echo "✓ Successfully pulled from GitHub"
    echo ""
    
    # Step 4: Restart service if running
    browser="$(mpe_patch_browser_unit)"
    echo "[4/4] Restarting $browser..."
    if systemctl is-active --quiet "$browser"; then
        sudo systemctl restart "$browser"
        echo "✓ Service restarted"
    else
        echo "  - Service not currently running"
        echo "  - Will start on next boot or manual start"
    fi
    echo ""
else
    # Running from remote machine - use SSH
    # Step 3: Test connection to Pi
    echo "[3/5] Testing connection to Pi..."
    if [ -n "$SSH_KEY" ] && [ -f "$SSH_KEY" ]; then
        SSH_CMD="ssh -i $SSH_KEY"
        echo "  Using SSH key: $SSH_KEY"
    else
        SSH_CMD="ssh"
        echo "⚠️  SSH key not found, using default authentication"
        echo "  (Set SSH_KEY environment variable to specify key location)"
    fi

    if ! $SSH_CMD -o ConnectTimeout=5 ${PI_USER}@${PI_HOST} "echo 'Connected'" > /dev/null 2>&1; then
        echo "❌ ERROR: Cannot connect to ${PI_USER}@${PI_HOST}"
        echo ""
        echo "Troubleshooting:"
        echo "  1. Check Pi is powered on"
        echo "  2. Try: $SSH_CMD ${PI_USER}@${PI_HOST}"
        echo "  3. Or use IP: export PI_HOST=192.168.1.203"
        exit 1
    fi
    echo "✓ Connected to Pi"
    echo ""

    # Step 4: Pull from GitHub on Pi
    echo "[4/5] Pulling latest changes from GitHub on Pi..."
    $SSH_CMD ${PI_USER}@${PI_HOST} << ENDSSH
        set -e
        cd ${PI_REPO_REMOTE}
        
        # Check if repo exists
        if [ ! -d ".git" ]; then
            echo "❌ ERROR: Git repo not found at ${PI_REPO_PATH}"
            echo "Please clone the repo first or check the path"
            exit 1
        fi
        
        # Pull latest changes
        echo "  - Fetching from GitHub..."
        git fetch origin
        
        echo "  - Pulling latest changes..."
        git pull origin main || git pull origin master
        
        echo "✓ Successfully pulled from GitHub"
ENDSSH

    if [ $? -ne 0 ]; then
        echo "❌ ERROR: Failed to pull from GitHub on Pi"
        exit 1
    fi
    echo ""

    # Step 5: Restart service if running
    echo "[5/5] Restarting patch browser UI..."
    $SSH_CMD ${PI_USER}@${PI_HOST} bash -s <<EOF
$(mpe_pi_source_line)
source "\$MPE_MODULE_REPO/scripts/lib/mpe-services.sh"
browser=\$(mpe_patch_browser_unit)
if systemctl is-active --quiet "\$browser"; then
    sudo systemctl restart "\$browser"
    echo "✓ Restarted \$browser"
else
    echo "  - \$browser not currently running"
fi
EOF
    echo ""
fi

# Summary
echo "========================================"
echo "Deployment Complete!"
echo "========================================"
echo ""
echo "Changes deployed:"
echo "  ✓ Reduced button debounce: 300ms → 10ms"
echo "  ✓ Reduced encoder debounce: 30ms → 5ms"
echo ""
echo "These settings work with your hardware capacitors (103) for debouncing."
echo ""
if [ "$RUNNING_ON_PI" = true ]; then
    echo "To check service status:"
    echo "  sudo systemctl status patch-browser.service"
    echo ""
    echo "To view logs:"
    echo "  sudo journalctl -u patch-browser.service -f"
else
    echo "To check service status:"
    echo "  $SSH_CMD ${PI_USER}@${PI_HOST}"
    echo "  sudo systemctl status patch-browser.service"
    echo ""
    echo "To view logs:"
    echo "  $SSH_CMD ${PI_USER}@${PI_HOST} 'sudo journalctl -u patch-browser.service -f'"
fi
echo ""
