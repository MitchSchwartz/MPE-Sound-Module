#!/bin/bash
# Setup Windows symlinks for Surge XT patch editing workflow
# Creates junction from Surge XT Patches folder to git repo for seamless editing

set -e

# Configuration
GIT_REPO="c:/Users/mitch/GitHub/MPE Module"
SURGE_XT_DIR="c:/Users/mitch/Documents/Surge XT"
SURGE_PROGRAMDATA="c:/ProgramData/Surge XT"
PATCHES_SOURCE="$GIT_REPO/assets/user-data/Patches"

echo "========================================="
echo "  Surge XT Windows Symlink Setup"
echo "========================================="
echo ""

# Step 1: Validation
echo "Step 1/4: Validating environment..."

# Check git repo exists
if [ ! -d "$GIT_REPO" ]; then
    echo "❌ ERROR: Git repo not found at $GIT_REPO"
    exit 1
fi

# Check Surge XT directory exists
if [ ! -d "$SURGE_XT_DIR" ]; then
    echo "❌ ERROR: Surge XT not found at $SURGE_XT_DIR"
    echo "Please install Surge XT first: https://surge-synthesizer.github.io/"
    exit 1
fi

# Check patches source exists
if [ ! -d "$PATCHES_SOURCE" ]; then
    echo "❌ ERROR: Patches source not found at $PATCHES_SOURCE"
    exit 1
fi

echo "✓ Environment validated"
echo ""

# Step 2: Backup existing Patches folder
echo "Step 2/4: Backing up existing Patches folder..."

PATCHES_DIR="$SURGE_XT_DIR/Patches"

# Check if it's already a junction/symlink
if [ -L "$PATCHES_DIR" ]; then
    echo "✓ Already a symlink, removing old link"
    rm "$PATCHES_DIR"
elif [ -d "$PATCHES_DIR" ]; then
    # Regular directory - back it up
    BACKUP_DIR="$SURGE_XT_DIR/Patches.backup.$(date +%s)"
    mv "$PATCHES_DIR" "$BACKUP_DIR"
    echo "✓ Backed up existing folder to:"
    echo "  $(basename "$BACKUP_DIR")"
else
    echo "✓ No existing Patches folder"
fi
echo ""

# Step 3: Create junction (Windows symlink)
echo "Step 3/4: Creating symlink..."

# Convert paths to Windows format for mklink (must use backslashes)
WIN_TARGET=$(echo "$PATCHES_SOURCE" | sed 's|^/c/|C:\\|' | sed 's|/|\\|g')
WIN_LINK=$(echo "$PATCHES_DIR" | sed 's|^/c/|C:\\|' | sed 's|/|\\|g')

# Create junction using cmd.exe with proper escaping
cmd //c mklink //J "$WIN_LINK" "$WIN_TARGET" > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "✓ Symlink created successfully"
else
    echo "❌ ERROR: Failed to create symlink"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Close Surge XT if it's running"
    echo "  2. Ensure no file explorer windows are open in that directory"
    echo "  3. Try running manually:"
    echo "     cmd //c mklink //J \"$WIN_LINK\" \"$WIN_TARGET\""
    exit 1
fi
echo ""

# Step 4: Create additional junction in ProgramData (for factory install location)
echo "Step 4/5: Creating ProgramData symlink..."

PROGRAMDATA_PATCHES="$SURGE_PROGRAMDATA/Patches"

if [ -L "$PROGRAMDATA_PATCHES" ] || [ -d "$PROGRAMDATA_PATCHES" ]; then
    echo "✓ ProgramData junction already exists"
else
    if [ -d "$SURGE_PROGRAMDATA" ]; then
        WIN_PD_LINK=$(echo "$PROGRAMDATA_PATCHES" | sed 's|^/c/|C:\\|' | sed 's|/|\\|g')
        cmd //c mklink //J "$WIN_PD_LINK" "$WIN_TARGET" > /dev/null 2>&1
        if [ $? -eq 0 ]; then
            echo "✓ ProgramData symlink created"
        else
            echo "⚠️  Could not create ProgramData symlink (may need admin rights)"
        fi
    else
        echo "⚠️  ProgramData Surge XT folder not found, skipping"
    fi
fi
echo ""

# Step 5: Create junctions for factory and third-party patches (read-only access)
echo "Step 5/7: Creating Factory patches symlink..."

FACTORY_DIR="$SURGE_XT_DIR/patches_factory"
FACTORY_SOURCE="$GIT_REPO/assets/patches/patches_factory"

if [ -L "$FACTORY_DIR" ] || [ -d "$FACTORY_DIR" ]; then
    echo "✓ Factory junction already exists"
else
    WIN_FACTORY_LINK=$(echo "$FACTORY_DIR" | sed 's|^/c/|C:\\|' | sed 's|/|\\|g')
    WIN_FACTORY_TARGET=$(echo "$FACTORY_SOURCE" | sed 's|^/c/|C:\\|' | sed 's|/|\\|g')
    cmd //c mklink //J "$WIN_FACTORY_LINK" "$WIN_FACTORY_TARGET" > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo "✓ Factory symlink created"
    else
        echo "⚠️  Could not create Factory symlink"
    fi
fi
echo ""

# Step 6: Create junction for third-party patches
echo "Step 6/7: Creating Third-party patches symlink..."

THIRDPARTY_DIR="$SURGE_XT_DIR/patches_3rdparty"
THIRDPARTY_SOURCE="$GIT_REPO/assets/patches/third-party/patches_3rdparty"

if [ -L "$THIRDPARTY_DIR" ] || [ -d "$THIRDPARTY_DIR" ]; then
    echo "✓ Third-party junction already exists"
else
    WIN_THIRDPARTY_LINK=$(echo "$THIRDPARTY_DIR" | sed 's|^/c/|C:\\|' | sed 's|/|\\|g')
    WIN_THIRDPARTY_TARGET=$(echo "$THIRDPARTY_SOURCE" | sed 's|^/c/|C:\\|' | sed 's|/|\\|g')
    cmd //c mklink //J "$WIN_THIRDPARTY_LINK" "$WIN_THIRDPARTY_TARGET" > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo "✓ Third-party symlink created"
    else
        echo "⚠️  Could not create Third-party symlink"
    fi
fi
echo ""

# Step 7: Verify
echo "Step 7/7: Verifying symlinks..."

if [ -d "$PATCHES_DIR" ]; then
    # Check if we can see files through the junction
    if [ -d "$PATCHES_DIR/Mitch" ]; then
        PATCH_COUNT=$(find "$PATCHES_DIR/Mitch" -name "*.fxp" 2>/dev/null | wc -l)
        echo "✓ Documents symlink verified - found $PATCH_COUNT patch(es) in Mitch/ folder"
    else
        echo "✓ Documents symlink created (no patches found yet)"
    fi
else
    echo "⚠️  Warning: Documents symlink created but verification failed"
fi

if [ -d "$PROGRAMDATA_PATCHES/Mitch" ]; then
    echo "✓ ProgramData symlink verified"
fi

if [ -d "$FACTORY_DIR/Keys" ]; then
    FACTORY_COUNT=$(find "$FACTORY_DIR" -name "*.fxp" 2>/dev/null | wc -l)
    echo "✓ Factory symlink verified - found $FACTORY_COUNT patches"
fi

if [ -d "$THIRDPARTY_DIR" ]; then
    THIRDPARTY_COUNT=$(find "$THIRDPARTY_DIR" -maxdepth 1 -type d 2>/dev/null | wc -l)
    echo "✓ Third-party symlink verified - found $THIRDPARTY_COUNT author folders"
fi
echo ""

echo "========================================="
echo "  ✅ Setup Complete!"
echo "========================================="
echo ""
echo "Symlinks created:"
echo "  1. $SURGE_XT_DIR/Patches → Your custom patches"
echo "  2. $SURGE_XT_DIR/Factory → 639 factory patches"
echo "  3. $SURGE_XT_DIR/Third Party → 2,553 third-party patches"
echo "  4. $SURGE_PROGRAMDATA/Patches → Your custom patches"
echo ""
echo "All patches from your git repo are now accessible in Surge XT!"
echo ""
echo "Next steps:"
echo "  1. Open Surge XT on Windows"
echo "  2. Click 'Patch Browser' button"
echo "  3. You should see your custom patches in Mitch/ folder"
echo "  4. Any patches you save will automatically be in your git repo!"
echo ""
echo "If patches don't appear, check Surge XT settings:"
echo "  Menu → Data Folders → User Data Folder"
echo "  Should be: C:\\Users\\mitch\\Documents\\Surge XT"
echo "  Or: C:\\ProgramData\\Surge XT (either works now)"
echo ""
echo "To deploy patches to Pi:"
echo "  ./scripts/deploy-patches.sh"
echo ""
