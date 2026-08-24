#!/usr/bin/env bash
# Snapshot laptop mpe-cli configs into state/ (no private keys — SSH_KEY path only).
#
#   ./scripts/provision/capture-laptop-mpe-config.sh
#   ./scripts/provision/capture-laptop-mpe-config.sh ~/my-backup-dir
#
# Backs up:
#   ~/.config/mpe/mpe.env.pi4
#   ~/.config/mpe/mpe.env.pi5
#   ~/.config/mpe/mpe.env (if present)
#
# See docs/LAPTOP-MPE-CLI.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MPE_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mpe"

OUTPUT="${1:-$REPO_ROOT/state/laptop-mpe-$(date +%Y-%m-%d)}"
mkdir -p "$OUTPUT"

_copy_if_exists() {
    local src="$1"
    local name="$2"
    if [ -f "$src" ]; then
        cp -a "$src" "$OUTPUT/$name"
        echo "  captured: $src -> $name"
        return 0
    fi
    echo "  skip (missing): $src"
    return 1
}

any=false
_copy_if_exists "$MPE_CONFIG_DIR/mpe.env.pi4" "mpe.env.pi4" && any=true || true
_copy_if_exists "$MPE_CONFIG_DIR/mpe.env.pi5" "mpe.env.pi5" && any=true || true
_copy_if_exists "$MPE_CONFIG_DIR/mpe.env" "mpe.env" && any=true || true

if [ -d "$REPO_ROOT/config/laptop" ]; then
    cp -a "$REPO_ROOT/config/laptop/." "$OUTPUT/examples/"
    echo "  copied repo examples/ -> examples/"
fi

cat >"$OUTPUT/README.md" <<EOF
# Laptop mpe-cli config capture

*Captured: $(date -Iseconds)*

Restore manually:

\`\`\`bash
mkdir -p ~/.config/mpe
cp mpe.env.pi4 ~/.config/mpe/
cp mpe.env.pi5 ~/.config/mpe/
\`\`\`

Use \`MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 mpe ping\` per host.

SSH \`Host\` blocks in \`~/.ssh/config\` are **not** captured — document aliases in \`docs/LAPTOP-MPE-CLI.md\`.
EOF

if [ "$any" = false ]; then
    echo ""
    echo "WARNING: no mpe.env.* files found under $MPE_CONFIG_DIR"
    echo "  Copy from config/laptop/mpe.env.*.example and edit."
fi

echo ""
echo "Laptop config snapshot: $OUTPUT"
