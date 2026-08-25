#!/usr/bin/env bash
# Golden-image manifest + external-state capture on a reference Pi.
# Read-only on the live appliance — does not sanitize, install licenses, or strip secrets.
#
#   sudo ./scripts/image/capture-golden.sh --platform auto
#   sudo ./scripts/image/capture-golden.sh --platform pi5 --write-manifest-only
#
# Before dd imaging (separate scripts — mutates the Pi):
#   sudo ./scripts/install-license-payload.sh
#   sudo ./scripts/provision/sanitize-for-clone.sh
#   sudo ./scripts/provision/sanitize-for-clone.sh --verify
#
# See docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLATFORM="auto"
DRY=false
MANIFEST_ONLY=false

usage() {
    echo "Usage: sudo $0 --platform {pi4|pi5|auto} [--dry-run] [--write-manifest-only]" >&2
    echo "  Captures manifest + external state only. Does not run sanitize-for-clone.sh." >&2
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
| Hostname | $(hostname) |
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

## Before \`dd\` (separate scripts — not part of capture-golden)

Run on the reference Pi **immediately before** poweroff and imaging:

\`\`\`bash
sudo ./scripts/install-license-payload.sh
sudo ./scripts/install-license-payload.sh --verify
sudo ./scripts/provision/sanitize-for-clone.sh
sudo ./scripts/provision/sanitize-for-clone.sh --verify
sudo poweroff
\`\`\`

\`sanitize-for-clone.sh\` strips machine-id, SSH host keys, Tailscale creds, WiFi PSKs, and shell history.
\`authorized_keys\` is kept by default. See \`docs/PI4-GOLDEN-IMAGE.md\`.

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
echo "capture-golden ($PLATFORM): manifest + state captured"
echo "  This script does not sanitize the Pi or install license payload."
echo ""
echo "  Before dd imaging (separate — mutates the Pi):"
echo "    sudo ./scripts/install-license-payload.sh && sudo ./scripts/install-license-payload.sh --verify"
echo "    sudo ./scripts/provision/sanitize-for-clone.sh && sudo ./scripts/provision/sanitize-for-clone.sh --verify"
echo "    sudo poweroff"
echo "  Then dd SD on laptop — see $OUTPUT_DIR/IMAGE-MANIFEST.md"
