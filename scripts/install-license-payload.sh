#!/usr/bin/env bash
# Install the GPL compliance payload onto the appliance.
#
#   sudo ./scripts/install-license-payload.sh
#   sudo ./scripts/install-license-payload.sh --verify
#   ./scripts/install-license-payload.sh --dry-run
#
# Puts license texts, the Surge build script, and the INSTALLED binary's provenance under
# /usr/share/doc/mpe/licenses/ so the corresponding-source duty is discharged at handoff
# rather than via a 3-year written offer (GPL-3.0 §6(b)).
#
# --verify asserts the payload is present and that PROVENANCE matches the binary actually
#          installed. Exit 1 on any mismatch. Run before imaging.
#
# See THIRD-PARTY-NOTICES.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DEST="${MPE_LICENSE_DIR:-/usr/share/doc/mpe/licenses}"
COMMON="/usr/share/common-licenses"
SURGE_COMMIT_DEFAULT="253f8d86"
SURGE_UPSTREAM="https://github.com/surge-synthesizer/surge.git"

DRY=false
VERIFY=false
FAIL=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=true; shift ;;
        --verify)  VERIFY=true; shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "Usage: $0 [--dry-run|--verify]" >&2; exit 2 ;;
    esac
done

if [ "$DRY" = false ] && [ "$VERIFY" = false ] && [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo), or use --dry-run / --verify." >&2
    exit 1
fi

_run() { if [ "$DRY" = true ]; then echo "would: $*"; else "$@"; fi; }
_fail() { echo "VERIFY FAIL: $*" >&2; FAIL=1; }

# Resolve the Surge binary that is actually installed on this machine.
_find_surge() {
    local c
    for c in "${SURGE_CLI:-}" \
             "$HOME/surge/build/surge_xt_products/surge-xt-cli" \
             /home/*/surge/build/surge_xt_products/surge-xt-cli \
             "$(command -v surge-xt-cli 2>/dev/null || true)"; do
        [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
    done
    return 1
}

# ---------------------------------------------------------------- verify
if [ "$VERIFY" = true ]; then
    echo "=== install-license-payload --verify ==="

    for f in GPL-3 GPL-2 LGPL-2.1 CORRESPONDING-SOURCE.md PROVENANCE.txt build-surge.sh; do
        [ -f "$DEST/$f" ] || _fail "missing $DEST/$f"
    done

    if surge="$(_find_surge)"; then
        if [ -f "$DEST/PROVENANCE.txt" ]; then
            live_sha="$(sha256sum "$surge" | awk '{print $1}')"
            rec_sha="$(awk -F= '/^sha256=/{print $2}' "$DEST/PROVENANCE.txt")"
            if [ -z "$rec_sha" ]; then
                _fail "PROVENANCE.txt has no sha256= line"
            elif [ "$live_sha" != "$rec_sha" ]; then
                _fail "PROVENANCE sha256 does not match the installed binary"
                echo "    installed: $live_sha" >&2
                echo "    recorded:  $rec_sha" >&2
                echo "    -> re-run 'sudo $0' after replacing the Surge binary." >&2
            fi
        fi
    else
        _fail "no surge-xt-cli found — cannot confirm provenance matches what ships"
    fi

    if [ "$FAIL" -eq 0 ]; then
        echo "install-license-payload --verify: ok (clone-safe for distribution)"
        exit 0
    fi
    echo "install-license-payload --verify: FAILED" >&2
    exit 1
fi

# ---------------------------------------------------------------- install
echo "=== install-license-payload ==="
_run mkdir -p "$DEST"

echo "--- license texts ---"
for lic in GPL-3 GPL-2 LGPL-2.1; do
    if [ -f "$COMMON/$lic" ]; then
        _run cp -f "$COMMON/$lic" "$DEST/$lic"
        echo "  $lic (from $COMMON)"
    else
        echo "  WARNING: $COMMON/$lic not found — license text NOT installed" >&2
        echo "           On Debian this ships in base-files. Investigate before distributing." >&2
    fi
done

echo "--- build script ---"
if [ -f "$REPO_ROOT/scripts/build-surge.sh" ]; then
    _run cp -f "$REPO_ROOT/scripts/build-surge.sh" "$DEST/build-surge.sh"
    echo "  build-surge.sh"
else
    echo "  ERROR: scripts/build-surge.sh not found — corresponding source incomplete." >&2
    exit 1
fi

echo "--- provenance (installed binary) ---"
surge="$(_find_surge || true)"
if [ -n "$surge" ]; then
    ver="$("$surge" --version 2>&1 | head -1 || echo unknown)"
    sha="$(sha256sum "$surge" | awk '{print $1}')"
    commit="$SURGE_COMMIT_DEFAULT"
    case "$ver" in *.*.*) c="${ver##*.}"; [ -n "$c" ] && commit="$c" ;; esac
    echo "  binary:  $surge"
    echo "  version: $ver"
    echo "  sha256:  $sha"
    if [ "$DRY" = false ]; then
        cat >"$DEST/PROVENANCE.txt" <<EOF
# Surge XT binary actually installed on this appliance
# Stamped by install-license-payload.sh at $(date -Is)
path=$surge
version=$ver
sha256=$sha
commit=$commit
upstream=$SURGE_UPSTREAM
EOF
    fi
else
    echo "  WARNING: no surge-xt-cli found — PROVENANCE.txt not stamped." >&2
    echo "           Re-run after deploying the binary; --verify will fail until then." >&2
fi

echo "--- corresponding source notice ---"
if [ "$DRY" = false ]; then
    cat >"$DEST/CORRESPONDING-SOURCE.md" <<EOF
# Corresponding source — Surge XT

Surge XT is licensed **GPL-3.0-or-later** and is redistributed here in compiled form.
This directory carries everything needed to rebuild that binary from source.

| Field | Value |
|---|---|
| Upstream | $SURGE_UPSTREAM |
| Commit | see \`commit=\` in \`PROVENANCE.txt\` (default $SURGE_COMMIT_DEFAULT) |
| Build script | \`build-surge.sh\` in this directory |
| License text | \`GPL-3\` in this directory |

Rebuild:

\`\`\`bash
git clone $SURGE_UPSTREAM surge-src
cd surge-src && git checkout <commit from PROVENANCE.txt>
git submodule update --init --recursive
./build-surge.sh --arch a76      # a72 for Pi 4, generic otherwise
\`\`\`

Surge is built **unmodified** from that commit. The build uses a reduced target set and an
\`-mcpu\` flag, which is why \`build-surge.sh\` is included: upstream source alone would not
let you reproduce this binary.

Surge XT is the work of the Surge Synth Team, originally released under GPL-3.0 by
Claes Johanson / Vember Audio. See \`/usr/share/doc/mpe/licenses/GPL-3\`.

Other GPL components (SooperLooper GPL-2.0, JACK2 GPL-2.0, libjack LGPL-2.1) are Debian
packages — their source is available from the Debian archive and their copyright files are
under \`/usr/share/doc/<package>/copyright\`.
EOF
fi
echo "  CORRESPONDING-SOURCE.md"

echo ""
echo "install-license-payload: done -> $DEST"
[ "$DRY" = true ] || echo "  Verify before imaging: sudo $0 --verify"
