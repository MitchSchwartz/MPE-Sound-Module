#!/usr/bin/env bash
# Post-git-sync hook for `mpe looper deploy` — checkout branch, fix Pi git conflicts, bootstrap looper.
set -euo pipefail

BRANCH="${1:-dev}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "looper-deploy: branch=$BRANCH @ $(git rev-parse --short HEAD 2>/dev/null || echo '?')"

# Pi census runs sometimes leave untracked copies of files now committed on dev/yolo branches.
if [ -d appliance-state/pi5-irq-census-2026-08-23 ]; then
    if git ls-files --error-unmatch appliance-state/pi5-irq-census-2026-08-23/interrupts-loaded.txt >/dev/null 2>&1; then
        git clean -fd -- appliance-state/pi5-irq-census-2026-08-23 >/dev/null 2>&1 || true
    fi
fi
if [ -f scripts/capture-pi5-irq-loaded.sh ]; then
    if git ls-files --error-unmatch scripts/capture-pi5-irq-loaded.sh >/dev/null 2>&1; then
        rm -f scripts/capture-pi5-irq-loaded.sh 2>/dev/null || true
    fi
fi

if [ -x "$REPO_ROOT/scripts/bootstrap-pi5-looper.sh" ]; then
    "$REPO_ROOT/scripts/bootstrap-pi5-looper.sh"
else
    echo "looper-deploy: no bootstrap-pi5-looper.sh — git sync only"
fi

# Restart the session (APC bench + HUD), because a deploy that leaves it alone
# is a deploy that did not happen.
#
# 2026-08-27: bootstrap restarts SooperLooper but nothing restarted
# looper-session.py, so a session started the previous evening kept driving the
# pads on code from eleven commits earlier — while the deploy printed success
# and the new SHA. Hours of a real-looking bug came out of that. Python holds
# its modules in memory: pulling new files onto the Pi changes nothing about a
# process that already imported the old ones.
#
# Only if the unit is loaded; a bare checkout or a bench-only host has no unit
# and must not fail the deploy over it.
if systemctl list-unit-files mpe-looper-session.service >/dev/null 2>&1; then
    if systemctl cat mpe-looper-session.service >/dev/null 2>&1; then
        was_active="$(systemctl is-active mpe-looper-session.service 2>/dev/null || true)"
        echo "looper-deploy: restarting mpe-looper-session.service (was: ${was_active:-unknown})"
        # These used to be `|| { echo WARN...; }`. A brace block's status is
        # its last command, so `echo` returned 0, `set -e` never fired, and the
        # deploy exited clean.
        #
        # 2026-08-30 is what that costs. The bench crashlooped on arrival
        # (`repaint_scenes(force=True)`, restart counter 32) while
        # `mpe looper deploy` printed its PASS lines and returned success. The
        # appliance was dead and every reading said it was fine — a deploy
        # result identical whether the instrument came back or not, which is
        # the one bug shape this project keeps paying for.
        #
        # A failed restart is a FAILED DEPLOY. The new code is on disk and the
        # process running the pads is not running it, which is strictly worse
        # than not deploying: the SHA says one thing and the instrument does
        # another. The host that legitimately has no session unit is already
        # excluded by the `list-unit-files` guard above.
        if [ -x "$REPO_ROOT/scripts/restart-looper-session.sh" ]; then
            restart_cmd=(bash "$REPO_ROOT/scripts/restart-looper-session.sh")
        else
            restart_cmd=(sudo systemctl restart mpe-looper-session.service)
        fi
        if ! "${restart_cmd[@]}"; then
            echo "looper-deploy: FAIL — looper session restart failed." >&2
            echo "  The pads are still running PRE-DEPLOY code. Check:" >&2
            echo "    systemctl status mpe-looper-session.service" >&2
            echo "    journalctl -u mpe-looper-session.service -n 50" >&2
            exit 1
        fi
        if ! systemctl is-active --quiet mpe-looper-session.service; then
            # Belt and braces: the unit can exit non-zero AFTER a restart that
            # reported success, which is exactly how a crashloop presents —
            # systemd returns from `restart` and the process dies milliseconds
            # later. Ask the question again, at the end, about the thing we
            # actually care about.
            echo "looper-deploy: FAIL — mpe-looper-session.service is not active" >&2
            echo "  after a restart that reported success (crashloop?). Check:" >&2
            echo "    journalctl -u mpe-looper-session.service -n 50" >&2
            exit 1
        fi
        echo "looper-deploy: session active on $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    fi
else
    echo "looper-deploy: no mpe-looper-session.service on this host — skipping"
fi
