#!/usr/bin/env bash
# Pre-image checklist + sanitization on a reference Pi before dd / imaging.
# Run ON the Pi (Mitch gate — mutates identity files).
#
#   sudo ./scripts/image/capture-golden.sh --platform auto
#   sudo ./scripts/image/capture-golden.sh --platform pi4 [--dry-run]
#   sudo ./scripts/image/capture-golden.sh --platform pi5 --write-manifest-only
#
# After this script: power off, dd the SD from the laptop, store .img.xz privately.
# Tailscale credentials and SSH host keys are stripped — not included in the image.
# See docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLATFORM="auto"
DRY=false
MANIFEST_ONLY=false

usage() {
    echo "Usage: sudo $0 --platform {pi4|pi5|auto} [--dry-run] [--write-manifest-only]" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --platform) PLATFORM="${2:-}"; shift 2 ;;
        --dry-run) DRY=true; shift ;;
        --write-manifest-only) MANIFEST_ONLY=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

case "$PLATFORM" in
    auto|pi4|pi5) ;;
    *) echo "ERROR: --platform must be pi4, pi5, or auto" >&2; exit 2 ;;
esac

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"
# shellcheck source=../lib/detect-pi-platform.sh
source "$SCRIPT_DIR/../lib/detect-pi-platform.sh"

_detected="$(mpe_detect_pi_platform)"
if [ "$PLATFORM" = auto ]; then
    PLATFORM="$_detected"
fi

if [ "$PLATFORM" = unknown ]; then
    echo "ERROR: could not detect platform — pass --platform pi4 or pi5" >&2
    exit 1
fi

model="$(mpe_pi_model_string)"
case "$PLATFORM" in
    pi4)
        if ! echo "$model" | grep -qi 'Raspberry Pi 4'; then
            echo "WARNING: --platform pi4 but model=$model" >&2
        fi
        img_prefix="mpe-pi4-golden"
        state_host="raspberrypi2"
        plat_label="Pi 4"
        ;;
    pi5)
        if ! echo "$model" | grep -qi 'Raspberry Pi 5'; then
            echo "WARNING: --platform pi5 but model=$model" >&2
        fi
        img_prefix="mpe-pi5-golden"
        state_host="raspberrypi5"
        plat_label="Pi 5"
        ;;
esac

OUTPUT_DIR="${MPE_GOLDEN_OUTPUT:-$REPO_ROOT/artifacts/golden-${PLATFORM}}"

mkdir -p "$OUTPUT_DIR"

_write_image_manifest() {
    local surge_ver git_rev cmdline plat_section surge_prov_section=""
    # shellcheck source=../lib/write-platform-manifest.sh
    source "$SCRIPT_DIR/../lib/write-platform-manifest.sh"

    surge_ver="$("$SURGE_CLI" --version 2>/dev/null || echo unknown)"
    git_rev="$(cd "$MPE_MODULE_REPO" && git rev-parse HEAD 2>/dev/null || echo unknown)"
    cmdline="$(tr '\0' ' ' < /proc/cmdline)"
    plat_section="$(mpe_platform_manifest_markdown)"

    if [ -f "$SCRIPT_DIR/../lib/surge-build-provenance.sh" ]; then
        # shellcheck source=../lib/surge-build-provenance.sh
        source "$SCRIPT_DIR/../lib/surge-build-provenance.sh"
        surge_prov_section="$(mpe_surge_provenance_markdown)"
        mpe_surge_provenance_json >"$OUTPUT_DIR/surge-provenance.json"
    fi

    cat >"$OUTPUT_DIR/IMAGE-MANIFEST.md" <<EOF
# ${plat_label} golden image manifest

*Generated: $(date -Iseconds)*

| Field | Value |
|---|---|
| Platform | ${PLATFORM} |
| Model | $model |
| Hostname (pre-sanitize) | $(hostname) |
| MPE-Module | $git_rev |
| Surge CLI | $surge_ver |
| cmdline | $cmdline |

EOF

    if [ -n "$surge_prov_section" ]; then
        cat >>"$OUTPUT_DIR/IMAGE-MANIFEST.md" <<EOF
## Surge CLI (installed)

$surge_prov_section

EOF
    fi

    cat >>"$OUTPUT_DIR/IMAGE-MANIFEST.md" <<EOF
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
sudo dd if=/dev/sdX of=~/${img_prefix}-\$(date +%Y%m%d).img bs=4M status=progress conv=fsync
xz -9 -T0 ~/${img_prefix}-*.img
\`\`\`

Store the \`.img.xz\` **privately** — includes Surge GPL binary.

## Flash + provision

\`\`\`bash
./scripts/image/build-appliance.sh --platform ${PLATFORM} \\
  --state state/${state_host}-YYYY-MM-DD
# or legacy flash-and-provision when available
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
echo "=== GPL compliance payload (an image is distribution) ==="
lic_args=""
[ "$DRY" = true ] && lic_args="--dry-run"
"$REPO_ROOT/scripts/install-license-payload.sh" $lic_args

echo ""
echo "=== Sanitize for golden clone (no Tailscale / no host keys in image) ==="
sanitize_args=""
[ "$DRY" = true ] && sanitize_args="--dry-run"
"$REPO_ROOT/scripts/provision/sanitize-for-clone.sh" $sanitize_args

if [ "$DRY" = false ]; then
    echo ""
    echo "=== Verify clone-safe state ==="
    "$REPO_ROOT/scripts/provision/sanitize-for-clone.sh" --verify

    echo ""
    echo "=== Verify GPL compliance payload matches the installed binary ==="
    "$REPO_ROOT/scripts/install-license-payload.sh" --verify
fi

echo ""
echo "capture-golden ($PLATFORM): ready for imaging"
echo "  1. sudo poweroff"
echo "  2. dd SD on laptop — see $OUTPUT_DIR/IMAGE-MANIFEST.md"
echo "  3. On each clone: sudo tailscale up  (fresh enrollment)"
