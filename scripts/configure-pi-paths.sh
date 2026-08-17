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
    _preserved_audio_profile=""
    _preserved_surge_buffer=""
    _preserved_surge_sample_rate=""
    if [ -f /etc/mpe/mpe.env ]; then
        _preserved_audio_profile="$(mpe_read_appliance_env_var MPE_AUDIO_PROFILE 2>/dev/null || true)"
        _preserved_surge_buffer="$(mpe_read_appliance_env_var MPE_SURGE_BUFFER_SIZE 2>/dev/null || true)"
        _preserved_surge_sample_rate="$(mpe_read_appliance_env_var MPE_SURGE_SAMPLE_RATE 2>/dev/null || true)"
    fi

    # Keys this script OWNS — it regenerates them from the values above, so the
    # old file's copies are stale by definition. Everything else in the existing
    # file is a deliberate appliance tuning and is carried forward verbatim.
    #
    # This used to be the other way round: a hand-maintained allowlist of four
    # keys survived and everything else was silently dropped. MPE_CPU_GOVERNOR
    # was not on that list, so every `--force` un-pinned the CPU governor — and
    # --force is what deploy-all.sh, deploy-crash-fixes.sh, deploy-boot-animation.sh,
    # setup-touch-pi.sh, `mpe engine sync-units` and the AGENTS.md "apply branch"
    # workflow all run. A routine deploy quietly reverted a latency fix, with no
    # message and nothing to grep for afterwards. MPE_JACK_BUFFER was in the same
    # position. Preserve-by-default means the next tunable added to mpe.env does
    # not have to be remembered here to survive.
    _owned_keys="MPE_PI_USER MPE_HOME MPE_MODULE_REPO MPE_PERSONAL_REPO \
MPE_SURGE_ROOT MPE_SURGE_DOCS MPE_SURGE_LOG MPE_FAVORITES_NAME MPE_UI_MODE \
MPE_AUDIO_PROFILE MPE_SURGE_BUFFER_SIZE MPE_SURGE_SAMPLE_RATE"
    _carried=""
    if [ -f /etc/mpe/mpe.env ]; then
        while IFS= read -r _line; do
            case "$_line" in
                ""|\#*) continue ;;
                *=*) ;;
                *) continue ;;
            esac
            _key="${_line%%=*}"
            case " $_owned_keys " in
                *" $_key "*) continue ;;
            esac
            _carried="${_carried}${_line}"$'\n'
        done < /etc/mpe/mpe.env
    fi
    if [ -n "$_preserved_audio_profile" ]; then
        MPE_AUDIO_PROFILE="$_preserved_audio_profile"
    fi
    if [ "$FORCE" = true ] || [ ! -f /etc/mpe/mpe.env ]; then
        echo "Writing /etc/mpe/mpe.env ..."
        {
            echo "MPE_PI_USER=$MPE_PI_USER"
            echo "MPE_HOME=$HOME"
            echo "MPE_MODULE_REPO=$MPE_MODULE_REPO"
            echo "MPE_PERSONAL_REPO=${MPE_PERSONAL_REPO:-$HOME/MPE-Library}"
            echo "MPE_SURGE_ROOT=$MPE_SURGE_ROOT"
            echo "MPE_SURGE_DOCS=\"$MPE_SURGE_DOCS\""
            echo "MPE_SURGE_LOG=$LOG_FILE"
            echo "MPE_FAVORITES_NAME=\"$MPE_FAVORITES_NAME\""
            echo "MPE_UI_MODE=\"$MPE_UI_MODE\""
            echo "MPE_AUDIO_PROFILE=${MPE_AUDIO_PROFILE:-standalone}"
            if [ -n "$_preserved_surge_buffer" ]; then
                echo "MPE_SURGE_BUFFER_SIZE=$_preserved_surge_buffer"
            else
                echo "MPE_SURGE_BUFFER_SIZE=1024"
            fi
            if [ -n "$_preserved_surge_sample_rate" ]; then
                echo "MPE_SURGE_SAMPLE_RATE=$_preserved_surge_sample_rate"
            else
                echo "MPE_SURGE_SAMPLE_RATE=48000"
            fi
            if [ -n "$_carried" ]; then
                echo ""
                echo "# Carried forward from the previous /etc/mpe/mpe.env."
                printf '%s' "$_carried"
            fi
        } | sudo tee /etc/mpe/mpe.env > /dev/null
        if [ -n "$_carried" ]; then
            echo "  preserved appliance tunings:" \
                 "$(printf '%s' "$_carried" | cut -d= -f1 | tr '\n' ' ')"
        fi
    else
        echo "Keeping existing /etc/mpe/mpe.env (use --force to rewrite paths; audio profile preserved on --force)"
    fi

    mpe_source_appliance_env

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

    mpe_retire_touch_shutdown_animation_unit
    sudo systemctl daemon-reload
    echo ""
    echo "Enabling services (MPE_UI_MODE=$MPE_UI_MODE)..."
    echo "Installing udev rules..."
    "$MPE_MODULE_REPO/scripts/install-udev-rules.sh"
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
