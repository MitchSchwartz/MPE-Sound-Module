#!/usr/bin/env bash
# Device-side steps of the library split. Run ON the device; not run by anything automatic.
#
#   library-migrate-device.sh status                      read-only report
#   library-migrate-device.sh snapshot                    save patch counts + settings hits (before)
#   library-migrate-device.sh clone  --dir DIR            clone the personal library beside the old copy
#   library-migrate-device.sh link   --dir DIR [--dry-run]  settings key + links, in that order
#   library-migrate-device.sh verify                      compare against the saved snapshot
#   library-migrate-device.sh rollback                    links removed, backup dirs and env put back
#   library-migrate-device.sh cleanup --old-copy DIR [--yes]  step 6; lists unless --yes (NOT reversible)
#
# Every step is idempotent and none restarts anything: the browser and Surge keep
# playing from the directories they already have open. Restart is a separate, manual
# act (docs/LIBRARY-MIGRATION.md). Environment, never committed anywhere:
#   MPE_LIBRARY_PERSONAL_URL   clone URL of the personal library (needed by `clone`)
#   MPE_LIBRARY_UPSTREAM_URL   clone URL of the template (added as `upstream`)
# `clone` needs a credential for the personal repo; arranging that is a human step.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

STATE_DIR="${MPE_LIBRARY_MIGRATION_DIR:-${MPE_HOME:-$HOME}/.mpe-library-migration}"
BEFORE="$STATE_DIR/before.json"

cmd="${1:-}"
[ $# -gt 0 ] && shift
DIR="" OLD="" DRY=0 YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="${2:?--dir needs a path}"; shift 2 ;;
        --old-copy) OLD="${2:?--old-copy needs a path}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        --yes) YES=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

lib() { python3 "$SCRIPT_DIR/mpe-library" "$@"; }

roots() {
    printf '%s\n' "$MPE_SURGE_RESOURCES/patches_factory" "$MPE_SURGE_RESOURCES/patches_3rdparty" "$MPE_SURGE_DOCS/Patches"
}

case "$cmd" in
status)
    echo "Disk free (home): $(df -h --output=avail "${MPE_HOME:-$HOME}" | tail -1 | tr -d ' ')"
    echo "git: $(git --version)"
    echo "MPE_PERSONAL_REPO: ${MPE_PERSONAL_REPO:-(unset)}"
    if [ -n "${MPE_PERSONAL_REPO:-}" ] && [ -e "$MPE_PERSONAL_REPO/.git" ]; then
        echo "  git checkout; remotes: $(git -C "$MPE_PERSONAL_REPO" remote | tr '\n' ' ')"
        echo "  branch: $(git -C "$MPE_PERSONAL_REPO" rev-parse --abbrev-ref HEAD), uncommitted: $(git -C "$MPE_PERSONAL_REPO" status --porcelain --untracked-files=no | wc -l) file(s)"
    elif [ -n "${MPE_PERSONAL_REPO:-}" ]; then
        echo "  not a git checkout"
    fi
    while IFS= read -r r; do
        if [ -L "$r" ]; then echo "  $r -> $(readlink "$r")"
        elif [ -d "$r" ]; then echo "  $r (real directory, $(find "$r" -name '*.fxp' | wc -l) .fxp)"
        else echo "  $r (absent)"; fi
    done < <(roots)
    [ -f "$BEFORE" ] && echo "snapshot: $BEFORE" || echo "snapshot: none yet (run: snapshot)"
    ;;
snapshot)
    mkdir -p "$STATE_DIR"
    if [ -f "$BEFORE" ]; then
        echo "A snapshot already exists ($BEFORE); keeping it. Delete it to take a new one."
    else
        lib verify --save "$BEFORE"
    fi
    ;;
clone)
    [ -n "$DIR" ] || { echo "ERROR: --dir DIR required (not the old copy's path)" >&2; exit 2; }
    if [ -e "$DIR/.git" ]; then
        echo "$DIR is already a git checkout; not touching it."
    else
        [ -n "${MPE_LIBRARY_PERSONAL_URL:-}" ] || { echo "ERROR: set MPE_LIBRARY_PERSONAL_URL" >&2; exit 2; }
        if [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
            echo "ERROR: $DIR exists and is not an empty directory; refusing to clone over it." >&2; exit 1
        fi
        git clone "$MPE_LIBRARY_PERSONAL_URL" "$DIR"
    fi
    if [ -n "${MPE_LIBRARY_UPSTREAM_URL:-}" ]; then
        if git -C "$DIR" remote get-url upstream >/dev/null 2>&1; then
            git -C "$DIR" remote set-url upstream "$MPE_LIBRARY_UPSTREAM_URL"
        else
            git -C "$DIR" remote add upstream "$MPE_LIBRARY_UPSTREAM_URL"
        fi
        git -C "$DIR" fetch upstream
    else
        echo "note: MPE_LIBRARY_UPSTREAM_URL not set; add the 'upstream' remote before restore/update"
    fi
    ;;
link)
    [ -n "$DIR" ] || { echo "ERROR: --dir DIR required" >&2; exit 2; }
    [ -f "$BEFORE" ] || { echo "ERROR: take a snapshot first (snapshot)" >&2; exit 1; }
    # Checks first, inside setup-pi-symlinks: a refusal changes nothing.
    "$SCRIPT_DIR/setup-pi-symlinks.sh" --local --dry-run --personal-repo "$DIR"
    if [ "$DRY" = 1 ]; then echo "(dry run complete)"; exit 0; fi
    "$SCRIPT_DIR/configure-pi-paths.sh" --local --paths-only --personal-repo "$DIR"
    "$SCRIPT_DIR/setup-pi-symlinks.sh" --local --personal-repo "$DIR"
    lib verify --compare "$BEFORE"
    echo "Linked. Nothing was restarted; restart Surge and the browser when ready."
    ;;
verify)
    [ -f "$BEFORE" ] || { echo "ERROR: no snapshot" >&2; exit 1; }
    lib verify --compare "$BEFORE"
    ;;
rollback)
    "$SCRIPT_DIR/setup-pi-symlinks.sh" --local --rollback
    # Put back only the MPE_PERSONAL_REPO value the oldest env backup holds; other
    # keys changed since then stay as they are.
    prev="$(ls -1 "${MPE_APPLIANCE_ENV_FILE:-/etc/mpe/mpe.env}".pre-library.* 2>/dev/null | sort | head -1 || true)"
    if [ -n "$prev" ]; then
        old_val="$(grep '^MPE_PERSONAL_REPO=' "$prev" | tail -1 | cut -d= -f2- || true)"
        if [ -n "$old_val" ] && [ -d "$old_val" ]; then
            "$SCRIPT_DIR/configure-pi-paths.sh" --local --paths-only --personal-repo "$old_val"
        else
            echo "note: original MPE_PERSONAL_REPO ('${old_val:-unset}') is not a directory; env left as is"
        fi
    fi
    echo "Rolled back. Restart Surge and the browser when ready."
    ;;
cleanup)
    [ -n "$OLD" ] || { echo "ERROR: --old-copy DIR required" >&2; exit 2; }
    [ -e "$OLD/.git" ] && { echo "ERROR: $OLD is a git checkout; this is only for the old plain copy" >&2; exit 1; }
    [ "$(readlink -f "$OLD")" = "$(readlink -f "${MPE_PERSONAL_REPO:-/nonexistent}")" ] && { echo "ERROR: $OLD is the active library" >&2; exit 1; }
    mapfile -t victims < <({ echo "$OLD"; ls -1d "$MPE_SURGE_RESOURCES"/patches_*.backup.* "$MPE_SURGE_DOCS"/Patches.backup.* 2>/dev/null || true; })
    echo "Would delete (NOT reversible once deleted; the library repo is the archive):"
    printf '  %s\n' "${victims[@]}"
    if [ "$YES" != 1 ]; then echo "Re-run with --yes to delete."; exit 0; fi
    rm -rf -- "${victims[@]}"
    echo "Deleted."
    ;;
*)
    sed -n '2,19p' "$0"
    exit 2
    ;;
esac
