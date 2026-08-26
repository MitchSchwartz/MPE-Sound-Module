#!/usr/bin/env bash
# Detect Raspberry Pi board family from device tree.
# shellcheck shell=bash
#
# Usage:
#   source scripts/lib/detect-pi-platform.sh
#   mpe_detect_pi_platform   # prints: pi4 | pi5 | unknown

mpe_pi_model_string() {
    tr -d '\0' < /proc/device-tree/model 2>/dev/null || true
}

mpe_detect_pi_platform() {
    local model
    model="$(mpe_pi_model_string)"
    case "$model" in
        *"Raspberry Pi 5"*) echo pi5 ;;
        *"Raspberry Pi 4"*) echo pi4 ;;
        *) echo unknown ;;
    esac
}

mpe_is_raspberry_pi() {
    local model
    model="$(mpe_pi_model_string)"
    case "$model" in
        *Raspberry\ Pi*) return 0 ;;
        *) return 1 ;;
    esac
}

# Git branch for appliance deploys — split by board until Pi 5 merges to main.
mpe_appliance_git_ref() {
    local plat="${1:-}"
    local repo_root="${2:-}"
    if [ -z "$plat" ]; then
        plat="$(mpe_detect_pi_platform)"
    fi
    if [ -z "$repo_root" ]; then
        repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    fi
    local ref_file="$repo_root/config/platform/appliance-git-ref.${plat}"
    if [ -f "$ref_file" ]; then
        grep -v '^#' "$ref_file" | grep -v '^[[:space:]]*$' | head -1
        return 0
    fi
    if [ -f "$repo_root/config/platform/appliance-git-ref" ]; then
        grep -v '^#' "$repo_root/config/platform/appliance-git-ref" | grep -v '^[[:space:]]*$' | head -1
        return 0
    fi
    echo main
}
