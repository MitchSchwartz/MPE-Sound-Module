#!/bin/bash
# backup_patches.sh
# Creates a complete backup of all Surge XT patch directories

BACKUP_DIR="$HOME/surge_patches_backup_$(date +%Y%m%d_%H%M%S)"

echo "=== Surge XT Patch Backup Utility ==="
echo ""
echo "Creating backup at: $BACKUP_DIR"
echo ""

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup directories
PATCH_DIRS=(
    "$HOME/surge/resources/data/patches_factory"
    "$HOME/surge/resources/data/patches_3rdparty"
    "$HOME/Documents/Surge XT/Patches"
)

total_size=0
total_files=0

for dir in "${PATCH_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        echo "Backing up: $dir"
        dir_name=$(basename "$dir")

        # Copy entire directory
        cp -r "$dir" "$BACKUP_DIR/$dir_name"

        # Count files and size
        files=$(find "$BACKUP_DIR/$dir_name" -name "*.fxp" | wc -l)
        size=$(du -sh "$BACKUP_DIR/$dir_name" | cut -f1)

        echo "  ✓ $files patches ($size)"
        total_files=$((total_files + files))
    else
        echo "Skipping missing directory: $dir"
    fi
done

echo ""
echo "✅ Backup complete!"
echo "   Total patches: $total_files"
echo "   Location: $BACKUP_DIR"
echo ""
echo "To restore from backup:"
echo "  cp -r $BACKUP_DIR/* ~/surge/resources/data/"
