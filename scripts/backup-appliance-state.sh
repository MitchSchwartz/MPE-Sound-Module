#!/usr/bin/env bash
# Pull unversioned runtime state off the appliance into appliance-state/.
#
#   ./scripts/backup-appliance-state.sh              # capture
#   ./scripts/backup-appliance-state.sh --check      # report drift, change nothing
#
# Run after any recalibration. Without this, appliance-state/ drifts from the
# appliance and a restore quietly reverts tuning that was never captured.
#
# Deliberately captures calibration ONLY. Not credentials (the appliance holds
# none — see docs/PI-GITHUB-ACCESS.md), not the Surge build, not MPE-Library.
# This repo is public; keep it that way.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/appliance-state/calibration"
PI="${MPE_PI_SSH:-raspberrypi2.local}"
PI_USER="${MPE_PI_USER:-mitch}"
TARGET="${PI_USER}@${PI}"

CHECK=false
[ "${1:-}" = "--check" ] && CHECK=true

if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$TARGET" true 2>/dev/null; then
    echo "ERROR: cannot reach $TARGET over SSH." >&2
    exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Fetching calibration state from $TARGET ..."
scp -q -o BatchMode=yes "$TARGET:~/surge-cli-calibration.log" "$tmp/" 2>/dev/null || {
    echo "WARNING: surge-cli-calibration.log not found on the appliance." >&2
}
ssh -o BatchMode=yes "$TARGET" 'cd ~ && tar cf - .patch_browser_calibration_backups 2>/dev/null' \
    | tar xf - -C "$tmp" 2>/dev/null || true
[ -d "$tmp/.patch_browser_calibration_backups" ] && \
    mv "$tmp/.patch_browser_calibration_backups" "$tmp/patch_browser_calibration_backups"

# Never let a credential ride along, whatever changes upstream.
if grep -rqiE 'ghp_|github_pat_|tskey-|BEGIN .*PRIVATE KEY' "$tmp" 2>/dev/null; then
    echo "ERROR: credential-shaped content in captured state — refusing to copy." >&2
    exit 1
fi

if [ "$CHECK" = true ]; then
    if diff -rq "$DEST" "$tmp" >/dev/null 2>&1; then
        echo "  no drift — appliance-state matches the appliance"
        exit 0
    fi
    echo "  DRIFT between appliance and appliance-state/:"
    diff -rq "$DEST" "$tmp" 2>&1 | sed 's/^/    /'
    echo "  Run without --check to update."
    exit 1
fi

mkdir -p "$DEST"
rm -rf "${DEST:?}"/*
cp -r "$tmp"/. "$DEST"/
echo "Captured into appliance-state/calibration:"
find "$DEST" -type f -printf '  %P (%s bytes)\n'
echo ""
echo "Review and commit if changed:  git -C \"$ROOT\" status appliance-state"
