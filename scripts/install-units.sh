#!/usr/bin/env bash
# Install the appliance's systemd units from systemd/ into /etc/systemd/system.
#
#   sudo ./scripts/install-units.sh            # install + reproduce enable state
#   sudo ./scripts/install-units.sh --dry-run  # show what would change
#   sudo ./scripts/install-units.sh --diff     # diff repo copies vs installed
#
# Idempotent. Reproduces the RECORDED enable state rather than enabling
# everything: three units are deliberately disabled on the appliance and one is
# static (no [Install] section). Blanket `systemctl enable *` would change the
# instrument's boot behaviour.
#
# Captured from the live appliance 2026-08-16. If you change enablement on the
# Pi, update ENABLED/DISABLED below or the next restore silently reverts it.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/systemd"
DEST="/etc/systemd/system"

# Units that must be enabled at boot.
ENABLED=(
    mpe-jackd
    surge-xt-cli
    surge-watchdog
    sl-watchdog
    surge-poly-governor
    mpe-cpu-governor
    mpe-audio-profile-sync
    mpe-pressure-remap
    mpe-shutdown-splash
    midi-clock-in
)

# Installed but deliberately NOT enabled. Present so the file exists for manual
# start or for a future profile that turns them on.
DISABLED=(
    midi-clock-out
    boot-animation
    mic-to-uac2-bridge
    # An eval bench. Started deliberately for a test, never at boot.
    mpe-bench
)

# No [Install] section — cannot be enabled, only pulled in by another unit.
STATIC=(
    foot-pedal
)

MODE="install"
case "${1:-}" in
    --dry-run) MODE="dry-run" ;;
    --diff)    MODE="diff" ;;
    "")        ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
esac

if [ "$MODE" = "install" ] && [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo) to install." >&2
    exit 1
fi

[ -d "$SRC" ] || { echo "ERROR: $SRC not found." >&2; exit 1; }

# The units hardcode /home/mitch. Fail loudly rather than installing units that
# point at a home directory that does not exist on this machine.
APPLIANCE_USER="${MPE_PI_USER:-mitch}"
if [ ! -d "/home/$APPLIANCE_USER" ]; then
    echo "ERROR: /home/$APPLIANCE_USER does not exist — units reference it in" >&2
    echo "       ExecStart and WorkingDirectory. Set MPE_PI_USER or create the user." >&2
    exit 1
fi

changed=0
for f in "$SRC"/*.service; do
    unit="$(basename "$f")"
    case "$MODE" in
        diff)
            if [ -f "$DEST/$unit" ]; then
                if ! diff -q "$f" "$DEST/$unit" >/dev/null 2>&1; then
                    echo "--- DRIFT: $unit"
                    diff -u "$DEST/$unit" "$f" || true
                    changed=1
                fi
            else
                echo "--- MISSING on system: $unit"
                changed=1
            fi
            ;;
        dry-run)
            if [ ! -f "$DEST/$unit" ]; then
                echo "  would install (new):     $unit"
            elif ! diff -q "$f" "$DEST/$unit" >/dev/null 2>&1; then
                echo "  would overwrite (drift): $unit"
            else
                echo "  unchanged:               $unit"
            fi
            ;;
        install)
            install -m 0644 -o root -g root "$f" "$DEST/$unit"
            echo "  installed: $unit"
            ;;
    esac
done

if [ "$MODE" != "install" ]; then
    [ "$MODE" = "diff" ] && [ "$changed" -eq 0 ] && echo "  no drift — installed units match the repo"
    exit 0
fi

# An enabled unit whose ExecStart does not exist is the worst of both worlds:
# `systemctl is-enabled` says enabled, nothing reports failed, and the thing
# never runs. That is how mpe-looper.service skipped every boot for five days
# unnoticed. Check before enabling, and say so loudly.
# Checks EVERY absolute path on the ExecStart line, not just the first token.
# `ExecStart=/usr/bin/python3 /path/to/script.py` would otherwise only verify the
# interpreter — which always exists — so a deleted script passed silently. That is
# the exact failure this guard exists to catch, and sl-watchdog.service is that
# shape, so the guard was blind on the unit it was written alongside.
echo "Checking ExecStart targets of units about to be enabled ..."
missing_exec=0
for u in "${ENABLED[@]}"; do
    exec_line="$(sed -n 's/^ExecStart=//p' "$SRC/$u.service" | head -1)"
    [ -n "$exec_line" ] || continue
    # Strip systemd's leading modifiers (-, @, :, +, !) before the executable.
    while [ -n "$exec_line" ]; do
        case "$exec_line" in
            [-@:+!]*) exec_line="${exec_line#?}" ;;
            *) break ;;
        esac
    done
    for tok in $exec_line; do
        case "$tok" in
            /*) ;;
            *) continue ;;
        esac
        if [ ! -e "$tok" ]; then
            echo "  WARNING: $u is enabled but ExecStart path is missing: $tok" >&2
            missing_exec=1
        fi
    done
done
if [ "$missing_exec" -eq 1 ]; then
    echo "  ^ These units will start-fail or silently skip. Fix before relying on" >&2
    echo "    this appliance being restored." >&2
fi

echo "Reloading systemd ..."
systemctl daemon-reload

echo "Applying recorded enable state ..."
for u in "${ENABLED[@]}"; do
    systemctl enable "$u.service" >/dev/null 2>&1 && echo "  enabled:  $u"
done
for u in "${DISABLED[@]}"; do
    systemctl disable "$u.service" >/dev/null 2>&1 || true
    echo "  disabled: $u (intentional)"
done
for u in "${STATIC[@]}"; do
    echo "  static:   $u (no [Install]; started on demand)"
done

echo ""
echo "Done. Units installed but NOT started — this script does not restart audio."
echo "Start the graph deliberately:"
echo "  sudo systemctl start mpe-jackd surge-xt-cli sl-watchdog"
echo "Verify:  mpe jack status   (expect xruns: 0)"
