#!/bin/bash
# Install or refresh Pi path config and systemd units.
# From PC:  ./scripts/configure-pi-paths.sh [--force]
# On Pi:    ./scripts/configure-pi-paths.sh --local [--force]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

FORCE=false
for arg in "$@"; do
    case "$arg" in
        --local) ;;
        --force) FORCE=true ;;
    esac
done

_is_pi() {
    [ -f /proc/device-tree/model ] && grep -qi raspberry /proc/device-tree/model 2>/dev/null
}

_run_on_pi() {
    MPE_PI_USER="${MPE_PI_USER:-$(whoami)}"
    MPE_SCRIPTS_DIR="$MPE_MODULE_REPO/scripts"

    if [ ! -d "$MPE_MODULE_REPO" ]; then
        echo "ERROR: MPE-Module not found at $MPE_MODULE_REPO"
        exit 1
    fi

    echo "Pi path configuration"
    echo "  User:           $MPE_PI_USER"
    echo "  MPE-Module:     $MPE_MODULE_REPO"
    echo "  Assets repo:    ${MPE_PERSONAL_REPO:-"(not cloned yet)"}"
    echo "  Surge root:     $MPE_SURGE_ROOT"
    echo "  Favorites name: $MPE_FAVORITES_NAME"
    echo "  UI mode:        $MPE_UI_MODE"
    echo "  Audio profile:  ${MPE_AUDIO_PROFILE:-standalone}"
    echo ""

    sudo mkdir -p /etc/mpe
    if [ "$FORCE" = true ] || [ ! -f /etc/mpe/mpe.env ]; then
        echo "Writing /etc/mpe/mpe.env ..."
        sudo tee /etc/mpe/mpe.env > /dev/null <<EOF
MPE_PI_USER=$MPE_PI_USER
MPE_HOME=$HOME
MPE_MODULE_REPO=$MPE_MODULE_REPO
MPE_PERSONAL_REPO=${MPE_PERSONAL_REPO:-$HOME/MPE-Library}
MPE_SURGE_ROOT=$MPE_SURGE_ROOT
MPE_SURGE_DOCS="$MPE_SURGE_DOCS"
MPE_SURGE_LOG=$LOG_FILE
MPE_FAVORITES_NAME="$MPE_FAVORITES_NAME"
MPE_UI_MODE="$MPE_UI_MODE"
MPE_AUDIO_PROFILE=${MPE_AUDIO_PROFILE:-standalone}
EOF
    else
        echo "Keeping existing /etc/mpe/mpe.env (use --force to rewrite)"
    fi

    _install_service() {
        local src="$1"
        local name
        name="$(basename "$src")"
        sed \
            -e "s|@MPE_PI_USER@|$MPE_PI_USER|g" \
            -e "s|@MPE_MODULE_REPO@|$MPE_MODULE_REPO|g" \
            -e "s|@MPE_SCRIPTS_DIR@|$MPE_SCRIPTS_DIR|g" \
            "$src" | sudo tee "/etc/systemd/system/$name" > /dev/null
        echo "  ✓ $name"
    }

    echo "Installing systemd units..."
    for svc in "$MPE_MODULE_REPO/config/"*.service; do
        [ -f "$svc" ] || continue
        _install_service "$svc"
    done

    _install_usb_gadget_dropin() {
        local dropin_dir="/etc/systemd/system/usb-audio-gadget.service.d"
        local mpe_home="${MPE_HOME:-$HOME}"
        sudo mkdir -p "$dropin_dir"
        sudo tee "$dropin_dir/home.conf" > /dev/null <<EOF
[Service]
Environment=HOME=$mpe_home
EOF
        echo "  ✓ usb-audio-gadget.service.d/home.conf (HOME=$mpe_home)"
    }
    _install_usb_gadget_dropin

    sudo systemctl daemon-reload
    echo ""
    echo "Enabling services (MPE_UI_MODE=$MPE_UI_MODE)..."
    if [ "$(_mpe_ui_mode_normalized)" = touch ]; then
        echo "Installing touch udev rules..."
        for rule in "$MPE_MODULE_REPO/config/99-backlight-permissions.rules" \
                    "$MPE_MODULE_REPO/config/99-usb-audio.rules" \
                    "$MPE_MODULE_REPO/config/99-roli-seaboard.rules"; do
            if [ -f "$rule" ]; then
                sudo cp "$rule" "/etc/udev/rules.d/$(basename "$rule")"
                echo "  ✓ $(basename "$rule")"
            fi
        done
        sudo udevadm control --reload-rules
        sudo udevadm trigger
    fi
    mpe_enable_core_services
    echo ""
    echo "Done. Restart: sudo systemctl restart surge-xt-cli $(mpe_patch_browser_unit)"
}

if [ "${1:-}" = "--local" ] || _is_pi; then
    _run_on_pi
else
    echo "Configuring Pi at $PI_USER@$PI_HOST via SSH..."
    _extra=""
    [ "$FORCE" = true ] && _extra=" --force"
    if [ -n "$PI_MPE_MODULE" ]; then
        mpe_pi_ssh "cd '$PI_MPE_MODULE' && ./scripts/configure-pi-paths.sh --local$_extra"
    else
        mpe_pi_ssh 'cd "${MPE_MODULE_REPO:-$HOME/MPE-Module}" && ./scripts/configure-pi-paths.sh --local'"$_extra"
    fi
fi
