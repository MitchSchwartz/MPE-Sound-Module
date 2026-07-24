#!/bin/bash
# Single path loader — PC and Pi. Override via config/mpe.env or /etc/mpe/mpe.env.

if [ -n "${BASH_SOURCE[0]}" ]; then
    _PATHS_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    _PATHS_LIB="$(cd "$(dirname "$0")/lib" && pwd)"
fi
_MPE_MODULE_ROOT="$(cd "$_PATHS_LIB/../.." && pwd)"

if [ -f /etc/mpe/mpe.env ]; then
    # shellcheck disable=SC1091
    source /etc/mpe/mpe.env
elif [ -f "${HOME:-/tmp}/.config/mpe/mpe.env" ]; then
    # shellcheck disable=SC1091
    source "${HOME}/.config/mpe/mpe.env"
fi

if [ -f "$_MPE_MODULE_ROOT/config/mpe.env" ]; then
    # shellcheck disable=SC1091
    source "$_MPE_MODULE_ROOT/config/mpe.env"
fi

MPE_MODULE_REPO="${MPE_MODULE_REPO:-$_MPE_MODULE_ROOT}"

MPE_PERSONAL_REPO="${MPE_PERSONAL_REPO:-}"
if [ -n "$MPE_PERSONAL_REPO" ] && [ -d "$MPE_PERSONAL_REPO" ]; then
    MPE_PERSONAL_REPO="$(cd "$MPE_PERSONAL_REPO" && pwd)"
elif [ -d "$MPE_MODULE_REPO/../MPE-Library" ]; then
    MPE_PERSONAL_REPO="$(cd "$MPE_MODULE_REPO/../MPE-Library" && pwd)"
elif [ -d "$MPE_MODULE_REPO/../MPE-Personal" ]; then
    MPE_PERSONAL_REPO="$(cd "$MPE_MODULE_REPO/../MPE-Personal" && pwd)"
fi
MPE_ASSETS_DIR="${MPE_PERSONAL_REPO:+$MPE_PERSONAL_REPO/assets}"

MPE_SURGE_ROOT="${MPE_SURGE_ROOT:-$HOME/surge}"
MPE_SURGE_RESOURCES="${MPE_SURGE_RESOURCES:-$MPE_SURGE_ROOT/resources/data}"
SURGE_CLI="${SURGE_CLI:-$MPE_SURGE_ROOT/build/surge_xt_products/surge-xt-cli}"
MPE_SURGE_DOCS="${MPE_SURGE_DOCS:-$HOME/Documents/Surge XT}"
MPE_SURGE_USER_DEFAULTS="${MPE_SURGE_USER_DEFAULTS:-$HOME/.local/share/Surge XT/SurgeXTUserDefaults.xml}"
LOG_FILE="${MPE_SURGE_LOG:-$HOME/surge-cli.log}"

PI_HOST="${PI_HOST:-surge.local}"
# No safe default: Raspberry Pi Imager requires a custom username per-device (no
# more shipped "pi" user), so this must come from config/mpe.env or the environment.
PI_USER="${PI_USER:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/surge_pi_key}"
PI_MPE_MODULE="${PI_MPE_MODULE:-}"
PI_MPE_PERSONAL="${PI_MPE_PERSONAL:-}"
MPE_FAVORITES_NAME="${MPE_FAVORITES_NAME:-!Quick Access}"
MPE_UI_MODE="${MPE_UI_MODE:-oled}"

if [ -z "${SURGE_XT_DIR:-}" ]; then
    if [ -n "${USERPROFILE:-}" ] && command -v cygpath >/dev/null 2>&1; then
        SURGE_XT_DIR="$(cygpath -u "$USERPROFILE")/Documents/Surge XT"
    else
        SURGE_XT_DIR="$HOME/Documents/Surge XT"
    fi
fi
if [ -n "${USERPROFILE:-}" ] && command -v cygpath >/dev/null 2>&1; then
    _default_programdata="$(cygpath -u "$USERPROFILE")/../ProgramData/Surge XT"
else
    _default_programdata="/c/ProgramData/Surge XT"
fi
SURGE_PROGRAMDATA="${SURGE_PROGRAMDATA:-$_default_programdata}"

mpe_require_personal() {
    if [ -z "$MPE_PERSONAL_REPO" ] || [ ! -d "$MPE_ASSETS_DIR" ]; then
        echo "ERROR: Private assets repo not found."
        echo "Clone it beside MPE-Module (e.g. ../mpe-assets) or set MPE_PERSONAL_REPO."
        exit 1
    fi
}

mpe_pi_repo_path() {
    if [ -n "$PI_MPE_MODULE" ]; then
        printf '%s' "$PI_MPE_MODULE"
    else
        printf '%s' '${MPE_MODULE_REPO:-$HOME/MPE-Module}'
    fi
}

mpe_pi_source_line() {
    printf 'source %s/scripts/lib/paths.sh' "$(mpe_pi_repo_path)"
}

mpe_pi_ssh() {
    if [ -z "$PI_USER" ]; then
        echo "ERROR: PI_USER not set." >&2
        echo "Copy config/mpe.env.example to config/mpe.env and set PI_USER to your Pi's login username." >&2
        exit 1
    fi
    ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "$@"
}

mpe_pi_bash() {
    local cmd="$1"
    mpe_pi_ssh "bash -lc '$(mpe_pi_source_line); ${cmd}'"
}
