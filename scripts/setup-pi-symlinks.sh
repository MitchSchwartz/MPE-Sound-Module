#!/bin/bash
# Point Surge's three patch roots at the personal library checkout.
#
#   ./scripts/setup-pi-symlinks.sh --local [--dry-run]      on the device
#   ./scripts/setup-pi-symlinks.sh --local --rollback       undo (backups put back)
#   ./scripts/setup-pi-symlinks.sh [--dry-run]              from the laptop, over SSH
#
# Flags:
#   --local            run here (the device). Without it, run the same thing over SSH.
#   --dry-run          run every check, change nothing.
#   --rollback         remove the links and rename the newest *.backup.<ts> back.
#   --restart          restart Surge and the patch browser afterwards (never by default:
#                      linking does not interrupt playing, a restart does).
#   --personal-repo P  library checkout to link to (default: $MPE_PERSONAL_REPO).
#
# Link names never change, so stable keys and every per-patch setting stay put.
# Refuses (changes nothing) if a live directory holds files the library tree lacks
# or holds a different version of: merge them in first. Idempotent.
# See docs/PATHS.md and docs/LIBRARY-MIGRATION.md.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/library-link.sh
source "$SCRIPT_DIR/lib/library-link.sh"

LOCAL=false
DRY_RUN=0
ROLLBACK=false
RESTART=false
REPO_ARG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --local) LOCAL=true; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --rollback) ROLLBACK=true; shift ;;
        --restart) RESTART=true; shift ;;
        --personal-repo) REPO_ARG="${2:?--personal-repo needs a path}"; shift 2 ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
export DRY_RUN

if [ "$LOCAL" = false ]; then
    _flags=""
    [ "$DRY_RUN" = 1 ] && _flags="$_flags --dry-run"
    [ "$ROLLBACK" = true ] && _flags="$_flags --rollback"
    [ "$RESTART" = true ] && _flags="$_flags --restart"
    [ -n "$REPO_ARG" ] && _flags="$_flags --personal-repo '$REPO_ARG'"
    echo "Running on $PI_USER@$PI_HOST ..."
    # shellcheck disable=SC2016  # expanded on the device, deliberately
    mpe_pi_ssh "cd \"$(mpe_pi_repo_path)\" && ./scripts/setup-pi-symlinks.sh --local$_flags"
    exit $?
fi

if [ "$ROLLBACK" = true ]; then
    # Needs no library: it only puts the *.backup.<ts> directories back.
    library_link_rollback
    echo "Rolled back. Restart Surge and the patch browser when ready."
    exit 0
fi

[ -n "$REPO_ARG" ] && MPE_PERSONAL_REPO="$(cd "$REPO_ARG" && pwd)"
if [ -z "$MPE_PERSONAL_REPO" ] || [ ! -d "$MPE_PERSONAL_REPO/assets" ]; then
    echo "ERROR: no library checkout with an assets/ dir (set MPE_PERSONAL_REPO or --personal-repo)." >&2
    exit 1
fi
ASSETS="$MPE_PERSONAL_REPO/assets"
export ASSETS

echo "Library: $MPE_PERSONAL_REPO"
if ! library_link_check; then
    echo "Nothing was changed." >&2
    exit 1
fi
library_link_apply
if [ "$DRY_RUN" = 1 ]; then
    echo "(dry run: nothing changed)"
    exit 0
fi
if [ "$RESTART" = true ]; then
    sudo systemctl restart surge-xt-cli
    # shellcheck source=lib/mpe-services.sh
    source "$SCRIPT_DIR/lib/mpe-services.sh"
    sudo systemctl restart "$(mpe_patch_browser_unit)"
else
    echo "Links are live for new reads. Restart Surge and the patch browser when ready (--restart)."
fi
