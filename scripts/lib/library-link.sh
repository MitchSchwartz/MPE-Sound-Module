#!/usr/bin/env bash
# Link Surge's three patch roots into a personal library checkout.
#
# Sourced by setup-pi-symlinks.sh. The LINK NAMES are what keep per-patch
# settings attached to their patches: the stable key's prefix comes from the
# name of the directory Surge scans (patches_factory -> factory,
# patches_3rdparty -> thirdparty, anything else -> user). Never rename a link.
#
# The key's path part is relative to the link. Third-party patches sit ONE level
# deep: the link points at `third-party/patches_3rdparty`, so authors sit directly
# under the scanned root and keys read `thirdparty:<author>/...`. Older deploys
# unpacked the whole `third-party/` dir, leaving devices with an extra level
# (patches_3rdparty/patches_3rdparty/<author>/...). The check accepts such a root
# when its single child of the same name matches the target; linking flattens it,
# so the stores' `thirdparty:patches_3rdparty/...` keys must be re-keyed first
# (`mpe-library rekey-thirdparty`, docs/LIBRARY-MIGRATION.md).
#
# Needs from the caller: MPE_SURGE_RESOURCES, MPE_SURGE_DOCS, ASSETS (the
# library's assets/ dir). Everything here is idempotent: a link already pointing
# at its target is left alone, and nothing is moved unless every root passed its
# checks first.

# "target-relative-to-assets|link-path" lines, one per root.
library_link_plan() {
    printf '%s|%s\n' \
        "patches/patches_factory" "$MPE_SURGE_RESOURCES/patches_factory" \
        "patches/third-party/patches_3rdparty" "$MPE_SURGE_RESOURCES/patches_3rdparty" \
        "user-data/Patches" "$MPE_SURGE_DOCS/Patches"
}

_ll_realpath() { readlink -f -- "$1" 2>/dev/null || echo "$1"; }

# Every non-directory under LIVE that TARGET lacks or holds different bytes for, one
# line each. Directories are not compared: one with no files below it holds nothing
# to lose (and git cannot carry it), so it never blocks a link; it stays in the backup.
# Not parsed from `diff -rq`: diffutils 3.11+ quotes names with spaces ("Surge XT"),
# which made a grep on its output miss files.
_ll_unsafe_files() {
    local live="$1" target="$2" f
    while IFS= read -r -d '' f; do
        f="${f#./}"
        if [ ! -e "$target/$f" ] && [ ! -L "$target/$f" ]; then
            echo "only on the device: $f"
        elif ! cmp -s -- "$live/$f" "$target/$f"; then
            echo "differs: $f"
        fi
    done < <(cd -- "$live" && find . ! -type d -print0)
}

# The tree under LIVE to compare with TARGET. Normally LIVE itself. A root holding
# exactly one entry, a directory named like the root itself, that TARGET does not
# also hold, is the old nested layout (patches_3rdparty/patches_3rdparty/<author>):
# its content is that child.
_ll_live_tree() {
    local live="$1" target="$2" name entries
    name="$(basename -- "$live")"
    entries="$(cd -- "$live" && find . -mindepth 1 -maxdepth 1 -printf '%f\n' | head -2)"
    if [ "$entries" = "$name" ] && [ -d "$live/$name" ] && [ ! -L "$live/$name" ] \
        && [ ! -e "$target/$name" ]; then
        printf '%s' "$live/$name"
    else
        printf '%s' "$live"
    fi
}

# Print one verdict per root: "ok", "link", or "refuse: <why>". Returns 1 if any refuse.
library_link_check() {
    local rc=0 rel link target unsafe tree note
    while IFS='|' read -r rel link; do
        target="$ASSETS/$rel"
        if [ ! -d "$target" ]; then
            echo "refuse: $link -> missing target tree $target"; rc=1; continue
        fi
        if [ -L "$link" ] && [ "$(_ll_realpath "$link")" = "$(_ll_realpath "$target")" ]; then
            echo "ok: $link already points at $target"; continue
        fi
        note=""
        if [ -d "$link" ]; then
            # A real directory, or a link to somewhere else: the target must already
            # hold every file in it (cd follows the link to its contents).
            if [ -n "$(find "$link/" ! -readable -print -quit 2>&1)" ]; then
                echo "refuse: $link has entries this user cannot read; cannot check it"
                rc=1; continue
            fi
            tree="$(_ll_live_tree "$link" "$target")"
            [ "$tree" != "$link" ] && note=" (nested layout: compared $tree; linking flattens it)"
            unsafe="$(_ll_unsafe_files "$tree" "$target")"
            if [ -n "$unsafe" ]; then
                echo "refuse: $tree has $(printf '%s\n' "$unsafe" | wc -l) file(s) the library lacks or holds different versions of:"
                printf '%s\n' "$unsafe" | head -10 | sed 's/^/    /'
                echo "    merge them into $target (and commit) first"
                rc=1; continue
            fi
        fi
        echo "link: $link -> $target$note"
    done < <(library_link_plan)
    return $rc
}

# A backup name that does not exist yet. (mv into an existing directory would nest
# the link inside it, so a same-second collision must never reuse a name.)
_ll_backup_name() {
    local base="$1.backup.$2" n=1 cand
    cand="$base"
    while [ -e "$cand" ] || [ -L "$cand" ]; do n=$((n + 1)); cand="$base-$n"; done
    printf '%s' "$cand"
}

# Link one root. On any failure leaves that root as it was and returns 1.
_ll_link_one() {
    local link="$1" target="$2" backup="$3" new="$1.new.$$"
    mkdir -p -- "$(dirname -- "$link")" || return 1
    rm -f -- "$new"
    ln -s -- "$target" "$new" || return 1
    if [ -e "$link" ] || [ -L "$link" ]; then
        mv -T -- "$link" "$backup" || { rm -f -- "$new"; return 1; }
    fi
    # Two renames, microseconds apart: the path exists at both ends.
    if ! mv -T -- "$new" "$link"; then
        rm -f -- "$new"
        [ -e "$backup" ] || [ -L "$backup" ] && mv -T -- "$backup" "$link" 2>/dev/null
        return 1
    fi
}

# Apply (or, with DRY_RUN=1, describe). The caller ran library_link_check first.
# All or nothing: if a root fails, the roots already linked are put back.
library_link_apply() {
    local rel link target ts backup
    local -a done_links=() done_backups=()
    ts="$(date +%s)"
    while IFS='|' read -r rel link; do
        target="$ASSETS/$rel"
        if [ -L "$link" ] && [ "$(_ll_realpath "$link")" = "$(_ll_realpath "$target")" ]; then
            continue
        fi
        backup="$(_ll_backup_name "$link" "$ts")"
        if [ "${DRY_RUN:-0}" = 1 ]; then
            if [ -e "$link" ] || [ -L "$link" ]; then echo "would move $link -> $backup"; fi
            echo "would link $link -> $target"
            continue
        fi
        if ! _ll_link_one "$link" "$target" "$backup"; then
            echo "ERROR: could not link $link; undoing the roots already linked." >&2
            local i
            for ((i = ${#done_links[@]} - 1; i >= 0; i--)); do
                rm -f -- "${done_links[$i]}"
                if [ -e "${done_backups[$i]}" ] || [ -L "${done_backups[$i]}" ]; then
                    mv -T -- "${done_backups[$i]}" "${done_links[$i]}"
                fi
                echo "  restored ${done_links[$i]}" >&2
            done
            return 1
        fi
        done_links+=("$link")
        done_backups+=("$backup")
        echo "linked $link -> $target (previous kept as $backup)"
    done < <(library_link_plan)
}

# Undo: put the newest *.backup.<ts> back in place of each link.
library_link_rollback() {
    local rel link backup
    while IFS='|' read -r rel link; do
        if [ ! -L "$link" ]; then
            echo "skip: $link is not a link (already rolled back or never linked)"; continue
        fi
        backup="$(ls -1d -- "$link".backup.* 2>/dev/null | sort | tail -1 || true)"
        if [ -z "$backup" ]; then
            echo "skip: $link has no backup to restore"; continue
        fi
        if [ "${DRY_RUN:-0}" = 1 ]; then
            echo "would restore $backup -> $link"; continue
        fi
        rm -- "$link"
        mv -- "$backup" "$link"
        echo "restored $link from $backup"
    done < <(library_link_plan)
}
