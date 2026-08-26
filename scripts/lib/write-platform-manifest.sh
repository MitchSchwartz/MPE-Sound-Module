#!/usr/bin/env bash
# Collect kernel/firmware/RT-kernel facts for manifests and golden-image audit.
# shellcheck shell=bash
#
# Usage:
#   source scripts/lib/write-platform-manifest.sh
#   mpe_platform_manifest_markdown >> MANIFEST.md
#   mpe_platform_manifest_json  > platform.json

mpe_platform_rt_kernel_note() {
    local plat kernel_pkg rt_pkg
    plat="${1:-unknown}"
    case "$plat" in
        pi5)
            kernel_pkg="linux-image-rpi-2712"
            rt_pkg="linux-image-rpi-2712-rt"
            ;;
        pi4)
            kernel_pkg="linux-image-rpi-v8"
            rt_pkg="linux-image-rpi-v8-rt"
            ;;
        *)
            kernel_pkg="(unknown)"
            rt_pkg="(unknown)"
            ;;
    esac

    if apt-cache show "$rt_pkg" >/dev/null 2>&1; then
        echo "**${rt_pkg}** is in apt ($(apt-cache policy "$rt_pkg" 2>/dev/null | awk '/Candidate:/{print $2; exit}' || echo '?'))."
    else
        echo "**${rt_pkg}** is **not in apt** (measured absence $(date +%Y-%m-%d)) — platform RT path unchanged until this flips."
    fi
    echo ""
    echo "Running kernel package: **$(dpkg-query -W -f='${Package} ${Version}' "$kernel_pkg" 2>/dev/null || echo "${kernel_pkg} not installed")**"
}

mpe_platform_manifest_markdown() {
    local plat model firmware mem
    plat="$(mpe_detect_pi_platform 2>/dev/null || echo unknown)"
    model="$(mpe_pi_model_string 2>/dev/null || tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
    firmware="$(vcgencmd version 2>/dev/null | head -1 || echo unknown)"
    mem="$(free -h | awk '/^Mem:/{print $2 " total / " $7 " avail"}' 2>/dev/null || echo unknown)"

    cat <<EOF
| Platform | $plat |
| Model | $model |
| Kernel (\`uname -r\`) | $(uname -r) |
| Firmware (\`vcgencmd version\`) | $firmware |
| RAM | $mem |

### Installed kernel packages

\`\`\`
$(dpkg -l 'linux-image-rpi-*' 2>/dev/null | awk '/^ii/{print $2, $3}' || echo "(none listed)")
\`\`\`

### RT kernel availability

$(mpe_platform_rt_kernel_note "$plat")
EOF
}

mpe_platform_manifest_json() {
    local plat model firmware kernel_pkg rt_in_apt
    plat="$(mpe_detect_pi_platform 2>/dev/null || echo unknown)"
    model="$(mpe_pi_model_string 2>/dev/null || echo unknown)"
    firmware="$(vcgencmd version 2>/dev/null | head -1 || echo unknown)"
    case "$plat" in
        pi5) kernel_pkg="linux-image-rpi-2712"; rt_pkg="linux-image-rpi-2712-rt" ;;
        pi4) kernel_pkg="linux-image-rpi-v8"; rt_pkg="linux-image-rpi-v8-rt" ;;
        *) kernel_pkg=""; rt_pkg="" ;;
    esac
    if [ -n "$rt_pkg" ] && apt-cache show "$rt_pkg" >/dev/null 2>&1; then
        rt_in_apt=true
    else
        rt_in_apt=false
    fi
    printf '{"platform":"%s","model":"%s","kernel":"%s","firmware":"%s","kernel_pkg":"%s","rt_pkg":"%s","rt_in_apt":%s,"captured":"%s"}\n' \
        "$plat" "$model" "$(uname -r)" "$firmware" "$kernel_pkg" "$rt_pkg" "$rt_in_apt" "$(date -Iseconds)"
}
