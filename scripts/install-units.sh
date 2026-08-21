#!/usr/bin/env bash
# Install the appliance's systemd units from config/ into /etc/systemd/system.
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
# config/ holds the templates and is the single source of truth. There used to be a
# second, pre-rendered copy in systemd/ that this script read instead — two committed
# copies of every unit, and adding to one silently missed the other (2026-08-17: the
# looper units landed in config/ only and this script failed with "No such file or
# directory" on the appliance). Rendering here means configure-pi-paths.sh and this
# script install byte-identical files from one source.
SRC="$ROOT/config"
DEST="/etc/systemd/system"

# Units that must be enabled at boot.
ENABLED=(
    mpe-jackd
    surge-xt-cli
    surge-watchdog
    surge-poly-governor
    mpe-cpu-governor
    mpe-audio-profile-sync
    mpe-pressure-remap
    mpe-shutdown-splash
    midi-clock-in
)

# Installed but deliberately NOT enabled. Present so the file exists for manual
# start or for a future profile that turns them on.
#
# Do NOT add mpe-peak-meter here. It gates on MPE_PEAK_METER inside
# start-mpe-peak-meter.sh (exit 0 when off), so it is safe to leave enabled — and
# listing it ran `systemctl disable` on every deploy, silently switching the OUT
# meter off again with MPE_PEAK_METER=1 still set and nothing in the journal.
# A unit that disables itself on each provisioning run is the ghost-unit pattern
# (Documents/DECISIONS.md 2026-08-15: a state that reads the same broken or fine).
DISABLED=(
    midi-clock-out
    boot-animation
    mic-to-uac2-bridge
    # mpe-bench retired 2026-08-17: it existed so a hardware test could free the APC
    # with `systemctl stop`, but the APC is now held by mpe-looper-session.service, so
    # stopping mpe-bench would have freed nothing. The agent's sudoers grant in
    # scripts/pi/provision-mpe-agent.sh names mpe-looper-session instead.
    # Phase 3M 2026-08-18: bench + HUD merged into mpe-looper-session.service.
    mpe-apc-bench
    sl-hud-monitor
    # The looper stack — installed, supervised, but NOT started at boot as of
    # 2026-08-18.
    #
    # CAVEAT: the measurement that motivated this is VOID. It was taken while
    # surge-watchdog's jack_lsp probe was the real xrun source (35/min); the run that
    # blamed the looper had stopped the looper AND both watchdogs together. With the
    # probe fixed (0f9875c) the appliance measures 0 xruns/min. The looper's own cost
    # has NOT been re-measured and may be negligible.
    #
    # Left opt-in deliberately: it is the state Mitch gigged on, and re-enabling it is
    # one command. Re-measure with scripts/midi-load.py before this counts against the
    # SooperLooper adopt/kill verdict (D15).
    # See docs/measurements/crackle-root-cause-2026-08-18.md.
    #
    # They still exist, still have Restart=always, and still self-repair once started
    # (the 2026-08-17 six-hour outage stays fixed). They simply do not come up on their
    # own, so the instrument boots clean and looping is an explicit choice:
    #   mpe looper on   ->  systemctl start mpe-sooperlooper mpe-looper-session sl-watchdog
    # See Documents/DECISIONS.md 2026-08-18 and DIRECTION.md D15 (adopt/kill verdict).
    mpe-sooperlooper
    mpe-looper-session
    sl-watchdog
    # Phase 1 snapshot publisher. Installed, not started: measured at 0.39% of a
    # core at 1 Hz — see docs/measurements/systemd-liveness-cost-2026-08-19.md —
    # which clears criterion 7 comfortably. But it is a new always-on poll on an
    # instrument whose scarcest resource is CPU, so switching it on is an operator
    # decision, not a deploy side effect. Enable with:
    #   sudo systemctl enable --now mpe-session-publisher
    mpe-session-publisher
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

# Template substitutions, matching configure-pi-paths.sh:_install_service so both
# installers produce identical files. MPE_MODULE_REPO defaults to the repo this
# script is running from, which is the only answer that cannot be wrong.
APPLIANCE_USER="${MPE_PI_USER:-mitch}"
if [ -z "${MPE_PI_USER:-}" ] && [ -f /etc/mpe/mpe.env ]; then
    _u="$(grep -E '^MPE_PI_USER=' /etc/mpe/mpe.env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'' || true)"
    [ -n "$_u" ] && APPLIANCE_USER="$_u"
fi
MODULE_REPO="${MPE_MODULE_REPO:-$ROOT}"
SCRIPTS_DIR="${MPE_SCRIPTS_DIR:-$MODULE_REPO/scripts}"

if [ ! -d "/home/$APPLIANCE_USER" ]; then
    echo "ERROR: /home/$APPLIANCE_USER does not exist — units reference it in" >&2
    echo "       ExecStart and WorkingDirectory. Set MPE_PI_USER or create the user." >&2
    exit 1
fi

render_unit() {
    sed \
        -e "s|@MPE_PI_USER@|$APPLIANCE_USER|g" \
        -e "s|@MPE_MODULE_REPO@|$MODULE_REPO|g" \
        -e "s|@MPE_SCRIPTS_DIR@|$SCRIPTS_DIR|g" \
        "$1"
}

RENDER_TMP="$(mktemp -d)"
trap 'rm -rf "$RENDER_TMP"' EXIT

changed=0
for f in "$SRC"/*.service; do
    unit="$(basename "$f")"
    rendered="$RENDER_TMP/$unit"
    render_unit "$f" > "$rendered"
    # A placeholder that survives rendering would install a unit pointing at a
    # literal "@MPE_...@" path — enabled, never running, exactly the ghost-unit
    # failure this repo already paid for once (a310449).
    if grep -q '@MPE_[A-Z_]*@' "$rendered"; then
        echo "ERROR: $unit still has unsubstituted placeholders after rendering:" >&2
        grep -o '@MPE_[A-Z_]*@' "$rendered" | sort -u | sed 's/^/       /' >&2
        exit 1
    fi
    case "$MODE" in
        diff)
            if [ -f "$DEST/$unit" ]; then
                if ! diff -q "$rendered" "$DEST/$unit" >/dev/null 2>&1; then
                    echo "--- DRIFT: $unit"
                    diff -u "$DEST/$unit" "$rendered" || true
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
            elif ! diff -q "$rendered" "$DEST/$unit" >/dev/null 2>&1; then
                echo "  would overwrite (drift): $unit"
            else
                echo "  unchanged:               $unit"
            fi
            ;;
        install)
            install -m 0644 -o root -g root "$rendered" "$DEST/$unit"
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
    # The RENDERED copy — the template's @MPE_MODULE_REPO@ is not a path on disk,
    # so checking the template would warn on every unit and mean nothing.
    exec_line="$(sed -n 's/^ExecStart=//p' "$RENDER_TMP/$u.service" | head -1)"
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

# Phase 3M upgrade: stop retired looper client units so they release UDP
# 9953 and the APC MIDI port before enabling the merged session.
RETIRED_LOOPER_CLIENTS=(mpe-apc-bench sl-hud-monitor)
for u in "${RETIRED_LOOPER_CLIENTS[@]}"; do
    if systemctl is-active --quiet "$u.service" 2>/dev/null; then
        echo "  stopping retired unit: $u (merged into mpe-looper-session)"
        systemctl stop --now "$u.service" 2>/dev/null || true
    fi
done

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
