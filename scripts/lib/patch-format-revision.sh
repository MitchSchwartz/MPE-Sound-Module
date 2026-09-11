#!/bin/bash
# Patch format revision gate — one decision, one place.
# shellcheck shell=bash
#
# Surge stamps its patch format version into every .fxp as `<patch revision="N">`.
# The number only migrates FORWARD: a newer engine reads an older patch and
# upgrades it on load. The reverse does not refuse — an older engine handed a
# newer patch loads what it recognises and silently defaults every parameter it
# does not. The patch appears in the browser. It plays. It sounds wrong. Nothing
# in the deploy, the browser, or the logs says a thing.
#
# That asymmetry is the whole reason this file exists. The appliance's engine is
# PINNED (surge-xt-cli @ 253f8d86, a pre-1.4 nightly — see THIRD-PARTY-NOTICES.md),
# so its ceiling is fixed until someone moves the pin deliberately. Patches are
# authored on the PC, where Surge is whatever the package manager last installed.
# The PC is the side that drifts upward, and deploy-patches.sh is the one door
# those files come through.
#
# Measured 2026-09-08: the PC's Surge XT 1.3.4 (flatpak) and the pinned nightly
# both write revision 24 — the ladder is level today, and nothing is currently
# blocked. This gate is here for the day a 1.4.x lands on the PC and raises it
# without announcing itself.
#
# Raising the pin is the ONLY correct way to raise this ceiling. Set
# MPE_PATCH_REVISION_MAX to override for a one-off; if you find yourself doing
# that twice, the pin moved and this default is now a lie.
#
# NAMING — "revision" is overloaded in this repo. _surge_revision() in
# measure-reference-suite.sh reads the BINARY's version string. This reads the
# PATCH FILE's format number. They are unrelated quantities.

MPE_PATCH_REVISION_MAX="${MPE_PATCH_REVISION_MAX:-24}"

# Print the format revision of one .fxp. Non-zero (and silent) when the file
# carries no readable `<patch revision=` header — a truncated or stub file
# reaches here as "unreadable", not as revision 0.
mpe_patch_revision() {
    local file="$1" rev
    [ -f "$file" ] || return 1
    rev="$(LC_ALL=C grep -a -o -m1 -E '<patch revision="[0-9]+"' "$file" 2>/dev/null)"
    rev="${rev//[!0-9]/}"
    [ -n "$rev" ] || return 1
    printf '%s\n' "$rev"
}

# Scan a tree of .fxp files against the ceiling. Offenders go to stderr, one per
# line. Returns 0 only when every patch is loadable by the pinned engine.
#
# Fails CLOSED on an unreadable file: a 3-byte stub is not a patch, and shipping
# one produces exactly the dead-on-arrival entry in the browser this guard is
# meant to prevent.
mpe_patch_revision_check() {
    local dir="$1" max="${2:-$MPE_PATCH_REVISION_MAX}"
    local bad=0 file rev
    [ -d "$dir" ] || return 0
    while IFS= read -r -d '' file; do
        if ! rev="$(mpe_patch_revision "$file")"; then
            printf '  unreadable (no <patch revision=> header): %s\n' "${file#"$dir"/}" >&2
            bad=$((bad + 1))
            continue
        fi
        if [ "$rev" -gt "$max" ]; then
            printf '  revision %s > %s: %s\n' "$rev" "$max" "${file#"$dir"/}" >&2
            bad=$((bad + 1))
        fi
    done < <(find "$dir" -type f -name '*.fxp' -print0 | sort -z)
    [ "$bad" -eq 0 ]
}
