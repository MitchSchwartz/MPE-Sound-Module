#!/usr/bin/env bash
# Verify a golden image manifest or document a pi-gen build (future).
#
#   ./scripts/image/bake-pi4-golden.sh verify
#   ./scripts/image/bake-pi4-golden.sh instructions
#
# Full image bake from scratch is not automated yet — use capture-pi4-golden.sh + dd.
# See docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MANIFEST="$REPO_ROOT/artifacts/golden-pi4/IMAGE-MANIFEST.md"

cmd="${1:-instructions}"

case "$cmd" in
    verify)
        if [ ! -f "$MANIFEST" ]; then
            echo "ERROR: missing $MANIFEST — run capture-pi4-golden.sh on the Pi first." >&2
            exit 1
        fi
        echo "=== Golden image manifest ==="
        cat "$MANIFEST"
        echo ""
        echo "Manual checks before publishing the .img.xz:"
        echo "  [ ] Surge version matches pinned release"
        echo "  [ ] MPE-Module git SHA is a release tag, not dev"
        echo "  [ ] cmdline includes irqaffinity=0,1"
        echo "  [ ] RESTORE rehearsal row filled in docs/RESTORE.md"
        ;;
    instructions)
        cat <<EOF
Pi 4 golden image — bake workflow (v1)

1. On the certified Pi 4 (raspberrypi2):
     cd ~/MPE-Module && git checkout main && git pull
     sudo ./scripts/provision/first-boot.sh --force   # if re-baking in place
     sudo ./scripts/image/capture-pi4-golden.sh

2. Power off, remove SD, dd on laptop:
     sudo dd if=/dev/sdX of=~/mpe-pi4-golden-\$(date +%Y%m%d).img bs=4M status=progress conv=fsync
     xz -9 -T0 ~/mpe-pi4-golden-*.img

3. Store .img.xz privately (Surge GPL binary inside).

4. Flash a blank SD with Raspberry Pi Imager or:
     xz -dc ~/mpe-pi4-golden-*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync

5. Boot, wait for SSH, then from laptop:
     ./scripts/image/flash-and-provision.sh \\
       --host newpi.local --user mitch \\
       --state state/raspberrypi2-YYYY-MM-DD

Future: pi-gen custom layer in artifacts/pi-gen/ (not shipped yet).
EOF
        ;;
    *)
        echo "Usage: $0 {verify|instructions}" >&2
        exit 2
        ;;
esac
