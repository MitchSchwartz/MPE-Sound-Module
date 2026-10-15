#!/usr/bin/env bash
# Copy MPE-Module state/ captures into the personal library repo (offsite backup).
#
#   ./scripts/provision/archive-state-to-assets.sh
#   ./scripts/provision/archive-state-to-assets.sh state/<pi5-host>-2026-08-23
#
# Destination: <personal repo>/state/appliance-state/captures/<date>/
# Captures live under state/, never assets/: they hold hundreds of .fxp backup copies
# that must not be mistaken for a patch root.
# Runs credential scan before copy. Does not commit: scripts/library-writeback.sh
# does that, or run git in the personal repo after review.
#
# See docs/BACKUP_GUIDE.md · docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATE_TAG="$(date +%Y-%m-%d)"

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"
# shellcheck source=lib/credential-scan.sh
source "$SCRIPT_DIR/lib/credential-scan.sh"

usage() {
    echo "Usage: $0 [STATE_DIR ...]" >&2
    echo "  Default: all dirs under $REPO_ROOT/state/" >&2
    exit 2
}

DEST_ROOT=""
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage ;;
        --dest) DEST_ROOT="${2:-}"; shift 2 ;;
        -*) echo "Unknown option: $1" >&2; usage ;;
        *) break ;;
    esac
done

if [ -z "$MPE_PERSONAL_REPO" ] || [ ! -d "$MPE_PERSONAL_REPO" ]; then
    echo "ERROR: personal library repo not found." >&2
    echo "  Clone it beside MPE-Module or set MPE_PERSONAL_REPO." >&2
    exit 1
fi

if [ -z "$DEST_ROOT" ]; then
    DEST_ROOT="$MPE_PERSONAL_REPO/state/appliance-state/captures/$DATE_TAG"
fi

mapfile -t SOURCES < <(
    if [ $# -gt 0 ]; then
        for s in "$@"; do
            if [[ "$s" != /* ]]; then s="$REPO_ROOT/$s"; fi
            [ -d "$s" ] && echo "$s"
        done
    else
        find "$REPO_ROOT/state" -mindepth 1 -maxdepth 1 -type d 2>/dev/null || true
    fi
)

if [ "${#SOURCES[@]}" -eq 0 ]; then
    echo "ERROR: no state capture directories found." >&2
    exit 1
fi

echo "=== credential scan (pre-archive) ==="
for src in "${SOURCES[@]}"; do
    echo "  scanning $(basename "$src") ..."
    _credential_scan_tree "$src"
done
echo "  clean"

mkdir -p "$DEST_ROOT"
for src in "${SOURCES[@]}"; do
    name="$(basename "$src")"
    echo "=== archive $name -> $DEST_ROOT/$name ==="
    rm -rf "$DEST_ROOT/$name"
    cp -a "$src" "$DEST_ROOT/$name"
done

cat >"$DEST_ROOT/README.md" <<EOF
# Appliance state captures — $DATE_TAG

Archived from \`MPE-Module/state/\` via \`archive-state-to-assets.sh\`.

| Tree | Source |
|------|--------|
$(for src in "${SOURCES[@]}"; do echo "| \`$(basename "$src")\` | laptop capture |"; done)

Restore on laptop:

\`\`\`bash
cp -a <personal-repo>/state/appliance-state/captures/$DATE_TAG/<host-label>-* MPE-Module/state/
./scripts/provision/apply-external-state.sh --state state/<pi5-host>-YYYY-MM-DD
\`\`\`

Credential scan passed before copy. SSH blocks hold \`IdentityFile\` paths only — no private keys.
EOF

echo ""
echo "Archived to: $DEST_ROOT"
echo "Next (in the personal repo): git add state/appliance-state && git commit && git push"
