#!/bin/bash
# Pull Quick Select + favorites index from Pi into the private assets repo (git backup).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
mpe_require_personal

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
DEST_ROOT="$MPE_ASSETS_DIR/user-data/quick-select"
SNAP="$DEST_ROOT/snapshots/$STAMP"
LATEST="$DEST_ROOT/latest"

echo "======================================="
echo "  Backup Quick Select → assets repo"
echo "======================================="
echo ""
echo "Pi: $PI_USER@$PI_HOST"
echo "Destination: $SNAP"
echo ""

if ! mpe_pi_ssh "echo Connected" >/dev/null; then
    echo "ERROR: Cannot connect to Pi"
    exit 1
fi

mkdir -p "$SNAP"

QA_REMOTE='${MPE_SURGE_DOCS:-$HOME/Documents/Surge XT}/Patches'
FAV_NAME="${MPE_FAVORITES_NAME#!}"

echo "Archiving Quick Select on Pi..."
mpe_pi_ssh bash -s <<EOF
set -e
$(mpe_pi_source_line)
QA="\${MPE_SURGE_DOCS:-\$HOME/Documents/Surge XT}/Patches/$FAV_NAME"
if [ ! -d "\$QA" ]; then
  echo "ERROR: Quick Select not found: \$QA" >&2
  exit 1
fi
cd "\$(dirname "\$QA")"
tar czf /tmp/quick-select-backup.tar.gz "$FAV_NAME"
EOF

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:/tmp/quick-select-backup.tar.gz" "$SNAP/" 
mpe_pi_ssh "rm -f /tmp/quick-select-backup.tar.gz"

(
  cd "$SNAP"
  tar xzf quick-select-backup.tar.gz
  rm quick-select-backup.tar.gz
)

scp -i "$SSH_KEY" "$PI_USER@$PI_HOST:.patch_browser_favorites.json" \
    "$SNAP/patch_browser_favorites.json" 2>/dev/null || {
    echo "WARN: favorites index not found on Pi — tree only"
}

cat >"$SNAP/README.txt" <<EOF
Quick Select backup pulled from $PI_HOST at $STAMP (UTC).
Commit this directory in MPE-Library (assets repo) after review.
Restore on Pi: python3 scripts/restore-quick-select.py $SNAP
EOF

rm -rf "$LATEST"
mkdir -p "$DEST_ROOT/snapshots"
cp -a "$SNAP" "$LATEST"
ln -sfn "snapshots/$STAMP" "$DEST_ROOT/LATEST" 2>/dev/null || true

COUNT="$(find "$SNAP/$FAV_NAME" -name '*.fxp' 2>/dev/null | wc -l | tr -d ' ')"
echo ""
echo "Backed up $COUNT .fxp + index → $SNAP"
echo ""
echo "Next — commit in assets repo:"
echo "  cd $MPE_PERSONAL_REPO"
echo "  git add assets/user-data/quick-select/"
echo "  git commit -m \"Quick Select backup $STAMP\""
echo "  git push"
