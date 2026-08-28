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
        if [ -x "$REPO_ROOT/scripts/restart-looper-session.sh" ]; then
            bash "$REPO_ROOT/scripts/restart-looper-session.sh" || {
                echo "looper-deploy: WARN — looper session restart failed;" >&2
                echo "  the pads may still be running pre-deploy code. Check:" >&2
                echo "    systemctl status mpe-looper-session.service" >&2
            }
        else
            sudo systemctl restart mpe-looper-session.service || {
                echo "looper-deploy: WARN — mpe-looper-session.service failed to restart;" >&2
                echo "  the pads may still be running pre-deploy code. Check:" >&2
                echo "    systemctl status mpe-looper-session.service" >&2
            }
        fi
    fi
else
    echo "looper-deploy: no mpe-looper-session.service on this host — skipping"
fi
