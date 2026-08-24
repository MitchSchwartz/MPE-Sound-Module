#!/usr/bin/env bash
# Snapshot laptop mpe-cli configs into state/ (no private keys — SSH_KEY path only).
#
#   ./scripts/provision/capture-laptop-mpe-config.sh
#   ./scripts/provision/capture-laptop-mpe-config.sh ~/my-backup-dir
#
# Backs up:
#   ~/.config/mpe/mpe.env.pi4 / .pi5 / mpe.env
#   ~/.ssh/config — pi4/pi5 Host blocks only (no keys)
#   shell aliases matching mpe4|mpe5 from ~/.zshrc / ~/.bashrc when present
#
# See docs/LAPTOP-MPE-CLI.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MPE_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mpe"
SSH_CONFIG="${HOME}/.ssh/config"

# shellcheck source=lib/credential-scan.sh
source "$SCRIPT_DIR/lib/credential-scan.sh"

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

_extract_ssh_host_blocks() {
    local out="$OUTPUT/ssh-config-mpe-hosts.txt"
    if [ ! -f "$SSH_CONFIG" ]; then
        echo "  skip (missing): $SSH_CONFIG"
        return 1
    fi
    awk '
        /^Host / {
            if (keep) print block
            block=$0
            keep=($0 ~ /(pi4|pi5|raspberrypi2|raspberrypi5|surge)/)
            next
        }
        {
            if (keep) block=block "\n" $0
        }
        END { if (keep) print block }
    ' "$SSH_CONFIG" >"$out"
    if [ -s "$out" ]; then
        echo "  captured: $SSH_CONFIG (pi4/pi5 Host blocks) -> ssh-config-mpe-hosts.txt"
        return 0
    fi
    rm -f "$out"
    echo "  skip: no pi4/pi5 Host blocks in $SSH_CONFIG"
    return 1
}

_extract_shell_aliases() {
    local rc out="$OUTPUT/shell-mpe-aliases.txt"
    for rc in "${ZDOTDIR:-$HOME}/.zshrc" "$HOME/.zshrc" "$HOME/.bashrc"; do
        [ -f "$rc" ] || continue
        if grep -E 'alias mpe[45]?=' "$rc" >/dev/null 2>&1; then
            {
                echo "# from $rc"
                grep -E 'alias mpe[45]?=' "$rc" || true
            } >>"$out"
        fi
    done
    if [ -f "$out" ]; then
        echo "  captured: mpe4/mpe5 aliases -> shell-mpe-aliases.txt"
        return 0
    fi
    echo "  skip: no mpe4/mpe5 aliases in shell rc"
    return 1
}

any=false
_copy_if_exists "$MPE_CONFIG_DIR/mpe.env.pi4" "mpe.env.pi4" && any=true || true
_copy_if_exists "$MPE_CONFIG_DIR/mpe.env.pi5" "mpe.env.pi5" && any=true || true
_copy_if_exists "$MPE_CONFIG_DIR/mpe.env" "mpe.env" && any=true || true
_extract_ssh_host_blocks && any=true || true
_extract_shell_aliases && any=true || true

if [ -d "$REPO_ROOT/config/laptop" ]; then
    mkdir -p "$OUTPUT/examples"
    cp -a "$REPO_ROOT/config/laptop/." "$OUTPUT/examples/"
    echo "  copied repo examples/ -> examples/"
fi

cat >"$OUTPUT/README.md" <<EOF
# Laptop mpe-cli config capture

*Captured: $(date -Iseconds)*

Restore manually:

\`\`\`bash
mkdir -p ~/.config/mpe
cp mpe.env.pi4 ~/.config/mpe/ 2>/dev/null || true
cp mpe.env.pi5 ~/.config/mpe/ 2>/dev/null || true
# Merge ssh-config-mpe-hosts.txt into ~/.ssh/config by hand
\`\`\`

Use \`MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 mpe ping\` per host.
EOF

if [ "$any" = false ]; then
    echo ""
    echo "WARNING: nothing captured — create configs from config/laptop/mpe.env.*.example"
fi

_credential_scan_tree "$OUTPUT"

echo ""
echo "Laptop config snapshot: $OUTPUT"
