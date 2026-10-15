#!/usr/bin/env bash
# Link Surge's three patch roots into a personal library checkout.
#
# Sourced by setup-pi-symlinks.sh. The LINK NAMES are what keep per-patch
# settings attached to their patches: the stable key's prefix comes from the
# name of the directory Surge scans (patches_factory -> factory,
# patches_3rdparty -> thirdparty, anything else -> user). Never rename a link.
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

# Print one verdict per root: "ok", "link", or "refuse: <why>". Returns 1 if any refuse.
library_link_check() {
    local rc=0 rel link target diffout
    while IFS='|' read -r rel link; do
        target="$ASSETS/$rel"
        if [ ! -d "$target" ]; then
            echo "refuse: $link -> missing target tree $target"; rc=1; continue
        fi
        if [ -L "$link" ] && [ "$(_ll_realpath "$link")" = "$(_ll_realpath "$target")" ]; then
            echo "ok: $link already points at $target"; continue
        fi
        if [ -d "$link" ] && [ ! -L "$link" ]; then
            # Real directory: the target must already hold everything in it.
            diffout="$(diff -rq -- "$link" "$target" 2>&1 || true)"
            if printf '%s\n' "$diffout" | grep -qE "^Only in ${link//\//\\/}|^Files .* differ$"; then
                echo "refuse: $link has files the library lacks or holds different versions of:"
                printf '%s\n' "$diffout" | grep -E "^Only in ${link//\//\\/}|^Files .* differ$" | head -10 | sed 's/^/    /'
                echo "    merge them into $target (and commit) first"
                rc=1; continue
            fi
        fi
        echo "link: $link -> $target"
    done < <(library_link_plan)
    return $rc
}

# Apply (or, with DRY_RUN=1, describe). The caller ran library_link_check first.
library_link_apply() {
    local rel link target ts new
    ts="$(date +%s)"
    while IFS='|' read -r rel link; do
        target="$ASSETS/$rel"
        if [ -L "$link" ] && [ "$(_ll_realpath "$link")" = "$(_ll_realpath "$target")" ]; then
            continue
        fi
        if [ "${DRY_RUN:-0}" = 1 ]; then
            if [ -e "$link" ] || [ -L "$link" ]; then echo "would move $link -> $link.backup.$ts"; fi
            echo "would link $link -> $target"
            continue
        fi
        mkdir -p -- "$(dirname -- "$link")"
        new="$link.new.$$"
        rm -f -- "$new"
        ln -s -- "$target" "$new"
        # Two renames, microseconds apart: the path exists at both ends.
        if [ -e "$link" ] || [ -L "$link" ]; then mv -- "$link" "$link.backup.$ts"; fi
        mv -T -- "$new" "$link"
        echo "linked $link -> $target (previous kept as $link.backup.$ts)"
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
