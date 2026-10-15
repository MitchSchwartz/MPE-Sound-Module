#!/usr/bin/env bash
# Copy the device's settings files into the personal library (settings/), or back.
#
#   ./scripts/library-snapshot-settings.sh --device-id ID            device home -> settings/
#   ./scripts/library-snapshot-settings.sh --from state/<capture> --device-id ID
#   ./scripts/library-snapshot-settings.sh --in --device-id ID       settings/ -> device home
#
# Settings are COPIED, never symlinked: every store writes with os.replace, which
# replaces a symlink with a regular file on its first save and silently detaches it.
# Copies land via a temp file and rename, so a reader never sees half a file.
#
# The file list is config/platform/external-state-paths.list (one source with the
# capture). This script only decides what each entry is:
#   synced   settings/<name without the leading dot>
#   device   settings/device/<device-id>/<name>   (DAC level, brightness, mpe.env)
#   skipped  runtime state, calibration backups, logs, looper audio, everything else
#
# --in refuses while the patch browser or Surge is running: the browser holds its
# stores in memory and would write them straight back (override: --force). Every live
# file it replaces is first copied to <home>/.mpe-library-backups/<timestamp>/.
# The MPE_FX_FILE / MPE_HOLD_FILE / MPE_PRESSURE_FILE / MPE_NORMALIZATION_FILE /
# MPE_FAVORITES_INDEX_FILE overrides are honoured for the live side, as the stores do.
#
# Options: --from DIR (a capture root with home/, or a home dir; default $MPE_HOME or
# $HOME), --to DIR (--in target home, default $MPE_HOME or $HOME), --dry-run,
# --repo PATH (default $MPE_PERSONAL_REPO), --device-id ID (or $MPE_DEVICE_ID; without
# one, device-bound files are skipped with a notice), --force.
# Reads and copies only; commits nothing (scripts/library-writeback.sh does).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATHS_LIST="${MPE_EXTERNAL_STATE_PATHS_LIST:-$REPO_ROOT/config/platform/external-state-paths.list}"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=provision/lib/credential-scan.sh
source "$SCRIPT_DIR/provision/lib/credential-scan.sh"

DIRECTION=out
FROM=""
TO=""
DRY=0
FORCE=0
REPO="${MPE_PERSONAL_REPO:-}"
DEVICE_ID="${MPE_DEVICE_ID:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --in) DIRECTION=in; shift ;;
        --from) FROM="${2:?--from needs a directory}"; shift 2 ;;
        --to) TO="${2:?--to needs a directory}"; shift 2 ;;
        --repo) REPO="${2:?--repo needs a path}"; shift 2 ;;
        --device-id) DEVICE_ID="${2:?--device-id needs a value}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$REPO" ] || [ ! -d "$REPO" ]; then
    echo "ERROR: no personal library checkout (set MPE_PERSONAL_REPO or --repo)." >&2
    exit 1
fi
SETTINGS="$REPO/settings"
HOME_DIR="${MPE_HOME:-$HOME}"

# Sets KIND (synced | device | skip), NAME (file name under settings/) and LIVE
# (path of the file below the home dir) for one list entry.
_classify() {
    KIND=skip NAME="" LIVE="$1"
    case "$1" in
        .patch_browser_favorites.json|.patch_browser_fx.json|.patch_browser_hold.json|\
.patch_browser_metadata.json|.patch_browser_normalization.json|.patch_browser_pressure.json|\
.patch_browser_ui.json|.patch_browser_volume.json|.mpe_sl_hud_state.json)
            KIND=synced NAME="${1#.}" ;;
        ".local/share/Surge XT")
            KIND=synced NAME=SurgeXTUserDefaults.xml LIVE="$1/SurgeXTUserDefaults.xml" ;;
        .patch_browser_device_volume.json|.patch_browser_brightness.json)
            KIND=device NAME="${1#.}" ;;
        /etc/mpe/mpe.env)
            KIND=device NAME=mpe.env ;;
    esac
}

# The store's own override for a live file, if the environment sets one.
_live_override() {
    local v=""
    case "$1" in
        .patch_browser_fx.json) v="${MPE_FX_FILE:-}" ;;
        .patch_browser_hold.json) v="${MPE_HOLD_FILE:-}" ;;
        .patch_browser_pressure.json) v="${MPE_PRESSURE_FILE:-}" ;;
        .patch_browser_normalization.json) v="${MPE_NORMALIZATION_FILE:-}" ;;
        .patch_browser_favorites.json) v="${MPE_FAVORITES_INDEX_FILE:-}" ;;
    esac
    [ -n "$v" ] && printf '%s' "${v/#\~/$HOME}"
    return 0
}

# Where an entry lives in the source: a capture keeps home files under home/ and
# /etc files under etc/; a live home dir uses the real /etc.
_home_path() {
    local rel="$1" ov
    if [ "$SRC_IS_LIVE" = 1 ]; then
        ov="$(_live_override "$rel")"
        if [ -n "$ov" ]; then printf '%s' "$ov"; return; fi
    fi
    case "$rel" in
        /etc/mpe/mpe.env)
            if [ -f "$SRC/etc/mpe/mpe.env" ]; then echo "$SRC/etc/mpe/mpe.env"; else echo "$rel"; fi ;;
        *) echo "$SRC_HOME/$rel" ;;
    esac
}

_copy() { # src dest
    local src="$1" dest="$2" tmp
    if [ -f "$dest" ] && cmp -s -- "$src" "$dest"; then
        echo "  same:    $dest"; return 0
    fi
    if [ "$DRY" = 1 ]; then echo "  would copy: $src -> $dest"; return 0; fi
    mkdir -p -- "$(dirname -- "$dest")"
    tmp="$dest.tmp.$$"
    cp -- "$src" "$tmp"
    mv -f -- "$tmp" "$dest"
    echo "  copied:  $dest"
}

_entries() { grep -v '^#' "$PATHS_LIST" | grep -v '^[[:space:]]*$' || true; }

if [ "$DIRECTION" = in ]; then
    DEST_IS_LIVE=0
    [ -z "$TO" ] && DEST_IS_LIVE=1
    if [ "$FORCE" = 0 ] && [ "$DRY" = 0 ]; then
        # shellcheck source=lib/mpe-services.sh
        source "$SCRIPT_DIR/lib/mpe-services.sh"
        for unit in "$(mpe_patch_browser_unit)" surge-xt-cli; do
            if systemctl is-active --quiet "$unit" 2>/dev/null; then
                echo "ERROR: $unit is running; it would write its stores back over these files." >&2
                echo "  Stop it first (sudo systemctl stop $unit), copy, then start it. --force to override." >&2
                exit 1
            fi
        done
    fi
    DEST_HOME="${TO:-$HOME_DIR}"
    BACKUP_DIR="$DEST_HOME/.mpe-library-backups/$(date +%Y%m%dT%H%M%S)"
    echo "settings/ -> $DEST_HOME"
else
    SRC="${FROM:-$HOME_DIR}"
    SRC_IS_LIVE=0
    if [ -d "$SRC/home" ]; then SRC_HOME="$SRC/home"; else SRC_HOME="$SRC"; SRC_IS_LIVE=1; fi
    echo "$SRC -> settings/"
fi

count=0
while IFS= read -r entry; do
    _classify "$entry"
    [ "$KIND" = skip ] && continue
    rel="$NAME"
    if [ "$KIND" = device ]; then
        if [ -z "$DEVICE_ID" ]; then
            echo "  skip (no --device-id): $entry"; continue
        fi
        rel="device/$DEVICE_ID/$NAME"
    fi

    if [ "$DIRECTION" = out ]; then
        src="$(_home_path "$LIVE")"
        if [ ! -f "$src" ]; then echo "  missing: $LIVE"; continue; fi
        _copy "$src" "$SETTINGS/$rel"
    else
        src="$SETTINGS/$rel"
        if [ ! -f "$src" ]; then echo "  absent in settings/: $rel"; continue; fi
        if [ "$entry" = /etc/mpe/mpe.env ]; then
            echo "  skip: /etc/mpe/mpe.env is restored with apply-external-state.sh, not copied here"; continue
        fi
        dest="$DEST_HOME/$LIVE"
        if [ "$DEST_IS_LIVE" = 1 ]; then
            ov="$(_live_override "$LIVE")"
            [ -n "$ov" ] && dest="$ov"
        fi
        if [ "$DRY" = 0 ] && [ -f "$dest" ] && ! cmp -s -- "$src" "$dest"; then
            mkdir -p -- "$BACKUP_DIR/$(dirname -- "$LIVE")"
            cp -p -- "$dest" "$BACKUP_DIR/$LIVE"
            echo "  kept old: $BACKUP_DIR/$LIVE"
        fi
        _copy "$src" "$dest"
    fi
    count=$((count + 1))
done < <(_entries)

if [ "$DIRECTION" = out ] && [ "$DRY" = 0 ] && [ -d "$SETTINGS" ]; then
    _credential_scan_tree "$SETTINGS"
fi
echo "done: $count file(s) handled"
