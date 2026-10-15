#!/usr/bin/env bash
# Laptop write-back: capture the device read-only and commit it into the personal repo.
#
#   ./scripts/library-writeback.sh --device-id ID [--push] [--no-capture] [--with-looper]
#
# 1. provision/capture-external-state.sh   (read-only on the device)
# 2. library-snapshot-settings.sh          captured settings -> settings/
# 3. provision/archive-state-to-assets.sh  capture tree -> state/appliance-state/captures/<date>/
# 4. git add on explicit paths (settings/ and state/ only) + one commit; --push to push.
#
# --no-capture reuses the newest state/<label>-* dir already on the laptop.
# Looper songs and clips (tens of MB of WAV) are left out of the commit: they belong in
# LFS, which is not set up yet. --with-looper includes them in plain git anyway.
# Patch edits made on the device (new .fxp, favorites copies) are NOT in a capture:
# they sit in the device's library checkout once it is a git checkout, where
# `git status` shows them. On-device auto-commit is a follow-up.
# Commits nothing outside settings/ and state/. Never forces a push.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

DEVICE_ID="${MPE_DEVICE_ID:-}"
PUSH=0
CAPTURE=1
WITH_LOOPER=0
while [ $# -gt 0 ]; do
    case "$1" in
        --device-id) DEVICE_ID="${2:?--device-id needs a value}"; shift 2 ;;
        --push) PUSH=1; shift ;;
        --no-capture) CAPTURE=0; shift ;;
        --with-looper) WITH_LOOPER=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$DEVICE_ID" ]; then
    echo "ERROR: --device-id (or MPE_DEVICE_ID) is required: device-bound settings are stored per device." >&2
    exit 2
fi
if [ -z "$MPE_PERSONAL_REPO" ] || [ ! -e "$MPE_PERSONAL_REPO/.git" ]; then
    echo "ERROR: MPE_PERSONAL_REPO is not a git checkout of the personal library." >&2
    exit 1
fi

# One date for the whole run: a midnight crossing must not split the archive and the trim.
DATE_TAG="$(date +%Y-%m-%d)"
export MPE_ARCHIVE_DATE_TAG="$DATE_TAG"
if [ "$CAPTURE" = 1 ]; then
    "$SCRIPT_DIR/provision/capture-external-state.sh"
fi
CAPTURE_DIR="$(ls -1dt "$REPO_ROOT"/state/*/ 2>/dev/null | head -1 || true)"
CAPTURE_DIR="${CAPTURE_DIR%/}"
if [ -z "$CAPTURE_DIR" ] || [ ! -d "$CAPTURE_DIR/home" ]; then
    echo "ERROR: no capture under $REPO_ROOT/state/." >&2
    exit 1
fi
echo "Using capture: $CAPTURE_DIR"

"$SCRIPT_DIR/library-snapshot-settings.sh" --from "$CAPTURE_DIR" --device-id "$DEVICE_ID" --repo "$MPE_PERSONAL_REPO"
"$SCRIPT_DIR/provision/archive-state-to-assets.sh" "$CAPTURE_DIR"

if [ "$WITH_LOOPER" = 0 ]; then
    # Only the archived copy in the library is trimmed; the laptop's own capture is untouched.
    rm -rf -- "$MPE_PERSONAL_REPO/state/appliance-state/captures/$DATE_TAG"/*/home/.mpe/looper-songs \
              "$MPE_PERSONAL_REPO/state/appliance-state/captures/$DATE_TAG"/*/home/.mpe/looper-clips
fi

cd "$MPE_PERSONAL_REPO"
git add -A -- settings state
if git diff --cached --quiet; then
    echo "Nothing changed since the last commit."
    exit 0
fi
git commit -q -m "device write-back $DATE_TAG ($DEVICE_ID)" -- settings state
echo "Committed: $(git rev-parse --short HEAD)"
if [ "$PUSH" = 1 ]; then
    git push "$MPE_LIBRARY_ORIGIN_REMOTE" HEAD
else
    echo "Not pushed (use --push)."
fi
