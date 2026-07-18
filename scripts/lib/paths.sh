#!/bin/bash
# Shared paths for MPE-Module (code) + MPE-Personal (private backup data)

if [ -n "${BASH_SOURCE[0]}" ]; then
    _PATHS_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    _PATHS_LIB="$(cd "$(dirname "$0")/lib" && pwd)"
fi

MPE_MODULE_REPO="${MPE_MODULE_REPO:-$(cd "$_PATHS_LIB/../.." && pwd)}"

if [ -n "${MPE_PERSONAL_REPO:-}" ]; then
    MPE_PERSONAL_REPO="$(cd "$MPE_PERSONAL_REPO" && pwd)"
elif [ -d "$MPE_MODULE_REPO/../MPE-Personal" ]; then
    MPE_PERSONAL_REPO="$(cd "$MPE_MODULE_REPO/../MPE-Personal" && pwd)"
else
    echo "ERROR: MPE-Personal repo not found."
    echo "Clone it beside MPE-Module or set MPE_PERSONAL_REPO."
    exit 1
fi

MPE_ASSETS_DIR="$MPE_PERSONAL_REPO/assets"
