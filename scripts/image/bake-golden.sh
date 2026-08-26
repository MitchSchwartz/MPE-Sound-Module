#!/usr/bin/env bash
# Verify a golden image manifest or print platform-specific bake instructions.
#
#   ./scripts/image/bake-golden.sh --platform pi4 verify
#   ./scripts/image/bake-golden.sh --platform pi5 instructions
#
# Full image bake from scratch is not automated yet — use capture-golden.sh + dd.
# See docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLATFORM=""
CMD="instructions"

usage() {
    echo "Usage: $0 --platform {pi4|pi5} {verify|instructions}" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --platform) PLATFORM="${2:-}"; shift 2 ;;
        verify|instructions) CMD="$1"; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

case "$PLATFORM" in
    pi4|pi5) ;;
    "") echo "ERROR: --platform pi4 or pi5 is required" >&2; exit 2 ;;
    *) echo "ERROR: --platform must be pi4 or pi5" >&2; exit 2 ;;
esac

# shellcheck source=../lib/detect-pi-platform.sh
source "$SCRIPT_DIR/../lib/detect-pi-platform.sh"
GIT_REF="$(mpe_appliance_git_ref "$PLATFORM" "$REPO_ROOT")"

case "$PLATFORM" in
    pi4)
        img_prefix="mpe-pi4-golden"
        ref_host="raspberrypi2"
        plat_label="Pi 4"
        ;;
    pi5)
        img_prefix="mpe-pi5-golden"
        ref_host="raspberrypi5"
        plat_label="Pi 5"
        ;;
esac

MANIFEST="$REPO_ROOT/artifacts/golden-${PLATFORM}/IMAGE-MANIFEST.md"
PROVENANCE_JSON="$REPO_ROOT/artifacts/golden-${PLATFORM}/surge-provenance.json"

_manifest_field() {
    local key="$1"
    awk -F'|' -v k="$key" '
        $2 ~ k {
            gsub(/^ +| +$/, "", $3)
            print $3
            exit
        }
    ' "$MANIFEST"
}

_expected_git_commit() {
    local ref="$1"
    (
        cd "$REPO_ROOT"
        git fetch origin "$ref" >/dev/null 2>&1 || true
        git rev-parse "origin/${ref}^{commit}" 2>/dev/null \
            || git rev-parse "${ref}^{commit}" 2>/dev/null \
            || echo unknown
    )
}

_verify_git_ref() {
    local manifest_rev expected_full expected_short
    manifest_rev="$(_manifest_field "MPE-Module")"
    [ -n "$manifest_rev" ] || { echo "FAIL: MANIFEST missing MPE-Module row"; return 1; }
    expected_full="$(_expected_git_commit "$GIT_REF")"
    expected_short="${expected_full:0:7}"
    if [ "$expected_full" = unknown ]; then
        echo "WARN: could not resolve git ref '$GIT_REF' locally — fetch origin/$GIT_REF"
        return 0
    fi
    case "$manifest_rev" in
        "$expected_short"|"$expected_full"|"${expected_full:0:${#manifest_rev}}")
            echo "OK: MPE-Module $manifest_rev matches ref $GIT_REF ($expected_short)"
            return 0
            ;;
        *)
            echo "FAIL: branch/ref mismatch — manifest has MPE-Module $manifest_rev, ref $GIT_REF is $expected_short"
            echo "      Re-run capture-golden on the Pi at: git checkout $GIT_REF && git reset --hard origin/$GIT_REF"
            return 1
            ;;
    esac
}

_verify_surge_arch() {
    local arch mcpu sha
    if [ -f "$PROVENANCE_JSON" ]; then
        arch="$(grep -o '"surge_arch":"[^"]*"' "$PROVENANCE_JSON" | cut -d'"' -f4 || true)"
        mcpu="$(grep -o '"surge_mcpu":"[^"]*"' "$PROVENANCE_JSON" | cut -d'"' -f4 || true)"
        sha="$(grep -o '"surge_sha256":"[^"]*"' "$PROVENANCE_JSON" | cut -d'"' -f4 || true)"
    else
        arch=""
        mcpu=""
        sha=""
    fi
    case "$PLATFORM" in
        pi5)
            if [ "$arch" = a76 ] || [ "$mcpu" = cortex-a76 ]; then
                echo "OK: Surge build arch a76 (${sha:0:12}…)"
                return 0
            fi
            echo "FAIL: Pi 5 golden image requires a76 Surge — manifest has arch=${arch:-unknown} mcpu=${mcpu:-unknown}"
            echo "      Run: build-surge.sh --arch a76 && install-surge-from-build.sh --arch a76"
            return 1
            ;;
        pi4)
            if [ "$arch" = a72 ] || [ "$mcpu" = cortex-a72 ] || [ "$arch" = generic ] || [ -z "$arch" ]; then
                echo "OK: Surge build acceptable for Pi 4 (arch=${arch:-generic/stock})"
                return 0
            fi
            echo "WARN: unexpected Surge arch for Pi 4: $arch"
            return 0
            ;;
    esac
}

_pi5_bake_content_gates() {
    echo ""
    echo "Pi 5 golden .img.xz — release assumptions:"
    if [ "$GIT_REF" = dev ]; then
        echo "  FAIL: appliance-git-ref.pi5 is dev — pin main (or a release tag) before baking"
        return 1
    fi
    echo "  OK: git ref $GIT_REF (release pin)"
    echo "  OK: governor tune (97/3/7 + ramp apply) — ship as-is; Gate B may continue on dev"
    echo "  OK: assumes 27 W / 5 A USB-C PSU (reference unit may differ during bring-up)"
    return 0
}

case "$CMD" in
    verify)
        if [ ! -f "$MANIFEST" ]; then
            echo "ERROR: missing $MANIFEST — run capture-golden.sh --platform $PLATFORM on the Pi first." >&2
            exit 1
        fi
        echo "=== Golden image manifest ($PLATFORM) ==="
        cat "$MANIFEST"
        echo ""
        echo "=== Automated checks ==="
        fail=0
        _verify_git_ref || fail=1
        _verify_surge_arch || fail=1
        if [ "$PLATFORM" = pi5 ]; then
            _pi5_bake_content_gates || fail=1
        fi
        echo ""
        echo "Manual checks before publishing the .img.xz:"
        echo "  [ ] Surge version matches pinned release (253f8d86)"
        echo "  [ ] cmdline / hygiene / units match production checklist"
        echo "  [ ] RESTORE rehearsal row filled in docs/RESTORE.md"
        echo "  [ ] install-license-payload.sh --verify passed on reference Pi before imaging"
        if [ "$fail" -ne 0 ]; then
            echo ""
            echo "VERIFY FAILED — do not publish .img.xz until checks pass."
            exit 1
        fi
        echo ""
        echo "VERIFY PASSED (automated + manual checklist still required)."
        ;;
    instructions)
        cat <<EOF
${plat_label} golden image — bake workflow (v1)

1. On the certified ${plat_label} reference unit:
     cd ~/MPE-Module && git checkout ${GIT_REF} && git pull
     sudo ./scripts/provision/first-boot.sh --force   # if re-baking in place
     sudo ./scripts/image/capture-golden.sh --platform ${PLATFORM}

2. When ready to image (separate scripts — mutates Pi):
     sudo ./scripts/install-license-payload.sh && sudo ./scripts/install-license-payload.sh --verify
     sudo ./scripts/provision/sanitize-for-clone.sh && sudo ./scripts/provision/sanitize-for-clone.sh --verify
     sudo poweroff

3. Remove SD, dd on laptop:
     sudo dd if=/dev/sdX of=~/${img_prefix}-\$(date +%Y%m%d).img bs=4M status=progress conv=fsync
     xz -9 -T0 ~/${img_prefix}-*.img

4. Store .img.xz privately (Surge GPL binary inside).

5. Flash a blank SD with Raspberry Pi Imager or:
     xz -dc ~/${img_prefix}-*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync

6. Boot, wait for SSH, then from laptop:
     ./scripts/image/build-appliance.sh --platform ${PLATFORM} \\
       --state state/${ref_host}-YYYY-MM-DD

Future: pi-gen custom layer in artifacts/pi-gen/ (not shipped yet).
EOF
        ;;
    *)
        usage
        ;;
esac
