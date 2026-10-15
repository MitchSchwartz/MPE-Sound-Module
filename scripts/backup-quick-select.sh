#!/bin/bash
# Pull Quick Select + favorites index from Pi into the personal library repo (git backup).
# Read-only on the device.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
mpe_require_personal

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
# Under state/, not assets/: snapshots hold .fxp copies and must never look like a patch root.
DEST_ROOT="$MPE_PERSONAL_REPO/state/quick-select-snapshots"
SNAP="$DEST_ROOT/snapshots/$STAMP"
LATEST="$DEST_ROOT/latest"

echo "======================================="
echo "  Backup Quick Select → personal library repo"
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

echo "Streaming Quick Select from the Pi (read-only: nothing is written on the device)..."
# tar to stdout over ssh: no scratch file on the device, unlike tar-to-/tmp + scp + rm.
mpe_pi_ssh bash -s <<EOF | tar xzf - -C "$SNAP"
set -e
$(mpe_pi_source_line)
QA="\${MPE_SURGE_DOCS:-\$HOME/Documents/Surge XT}/Patches/$FAV_NAME"
if [ ! -d "\$QA" ]; then
  # Favorites are pointers now: this folder only holds patches saved from a computer.
  echo "Quick Select folder not found (fine once favorites are pointers): \$QA" >&2
  tar czf - -T /dev/null
  exit 0
fi
cd "\$(dirname "\$QA")"
tar czf - "$FAV_NAME"
EOF

mpe_pi_ssh 'cat "${MPE_HOME:-$HOME}/.patch_browser_favorites.json"' \
    >"$SNAP/patch_browser_favorites.json" 2>/dev/null || {
    echo "WARN: favorites index not found on Pi — tree only"
}

cat >"$SNAP/README.txt" <<EOF
Quick Select backup pulled from $PI_HOST at $STAMP (UTC).
Commit this directory in the personal library repo after review.
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
echo "Next — commit in the personal library repo:"
echo "  cd $MPE_PERSONAL_REPO"
echo "  git add state/quick-select-snapshots/"
echo "  git commit -m \"Quick Select backup $STAMP\""
echo "  git push"
