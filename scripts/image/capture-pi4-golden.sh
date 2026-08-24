#!/usr/bin/env bash
# Pre-image checklist + sanitization on the reference Pi 4 before dd / imaging.
# Run ON the Pi (Mitch gate — mutates identity files).
#
#   sudo ./scripts/image/capture-pi4-golden.sh [--dry-run]
#   sudo ./scripts/image/capture-pi4-golden.sh --write-manifest-only
#
# After this script: power off, dd the SD from the laptop, store .img.xz privately.
# Tailscale credentials and SSH host keys are stripped — not included in the image.
# See docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="${MPE_GOLDEN_OUTPUT:-$REPO_ROOT/artifacts/golden-pi4}"
DRY=false
MANIFEST_ONLY=false

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=true; shift ;;
        --write-manifest-only) MANIFEST_ONLY=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"

model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
if ! echo "$model" | grep -qi 'Raspberry Pi 4'; then
    echo "WARNING: this script targets Pi 4; model=$model" >&2
fi

mkdir -p "$OUTPUT_DIR"

_write_image_manifest() {
    local surge_ver git_rev cmdline plat_section
    # shellcheck source=../lib/detect-pi-platform.sh
    source "$SCRIPT_DIR/../lib/detect-pi-platform.sh"
    # shellcheck source=../lib/write-platform-manifest.sh
    source "$SCRIPT_DIR/../lib/write-platform-manifest.sh"

    surge_ver="$("$SURGE_CLI" --version 2>/dev/null || echo unknown)"
    git_rev="$(cd "$MPE_MODULE_REPO" && git rev-parse HEAD 2>/dev/null || echo unknown)"
    cmdline="$(tr '\0' ' ' < /proc/cmdline)"
    plat_section="$(mpe_platform_manifest_markdown)"
    cat >"$OUTPUT_DIR/IMAGE-MANIFEST.md" <<EOF
# Pi 4 golden image manifest

*Generated: $(date -Iseconds)*

| Field | Value |
|---|---|
| Model | $model |
| Hostname (pre-sanitize) | $(hostname) |
| MPE-Module | $git_rev |
| Surge CLI | $surge_ver |
| cmdline | $cmdline |

## Platform / kernel

$plat_section

## Sanitization applied (never ship in image)

- \`/etc/machine-id\` truncated (+ \`/var/lib/dbus/machine-id\` when not symlinked)
- SSH **host** keys removed (regenerated on first boot)
- **Tailscale node credentials** removed (\`tailscale logout\` + \`/var/lib/tailscale/*\`)
- **NetworkManager WiFi profiles** removed (\`/etc/NetworkManager/system-connections/*\` — PSKs)
- Shell history truncated (\`~/.bash_history\`, \`~/.zsh_history\`)
- \`/var/lib/mpe/first-boot.stamp\` removed if present
- \`sanitize-for-clone.sh --verify\` run before poweroff (asserts the above)

\`authorized_keys\` is **kept** by default so your SSH key still works on first boot.
Use \`sanitize-for-clone.sh --strip-authorized-keys\` before imaging if you want a blank SSH slate.

## Imaging (laptop)

\`\`\`bash
# Pi powered off, SD in reader — adjust device node
sudo dd if=/dev/sdX of=~/mpe-pi4-golden-$(date +%Y%m%d).img bs=4M status=progress conv=fsync
xz -9 -T0 ~/mpe-pi4-golden-$(date +%Y%m%d).img
\`\`\`

Store the \`.img.xz\` **privately** — includes Surge GPL binary.

## Flash + provision

\`\`\`bash
./scripts/image/flash-and-provision.sh --host newpi.local --user mitch \\
  --state state/raspberrypi2-YYYY-MM-DD
\`\`\`

Each new unit: \`sudo tailscale up\` separately — credentials are never baked in.
EOF
    echo "Wrote $OUTPUT_DIR/IMAGE-MANIFEST.md"
    mpe_platform_manifest_json >"$OUTPUT_DIR/platform.json"
}

_write_image_manifest

if [ "$MANIFEST_ONLY" = true ]; then
    exit 0
fi

echo "=== Pre-image capture (external state) ==="
state_out="$REPO_ROOT/state/$(hostname)-pre-image-$(date +%Y-%m-%d)"
if [ "$DRY" = true ]; then
    echo "would: capture-external-state.sh --local $state_out"
else
    "$REPO_ROOT/scripts/provision/capture-external-state.sh" --local "$state_out"
fi

echo ""
echo "=== Sanitize for golden clone (no Tailscale / no host keys in image) ==="
sanitize_args=""
[ "$DRY" = true ] && sanitize_args="--dry-run"
"$REPO_ROOT/scripts/provision/sanitize-for-clone.sh" $sanitize_args

if [ "$DRY" = false ]; then
    echo ""
    echo "=== Verify clone-safe state ==="
    "$REPO_ROOT/scripts/provision/sanitize-for-clone.sh" --verify
fi

echo ""
echo "capture-pi4-golden: ready for imaging"
echo "  1. sudo poweroff"
echo "  2. dd SD on laptop — see $OUTPUT_DIR/IMAGE-MANIFEST.md"
echo "  3. On each clone: sudo tailscale up  (fresh enrollment)"
