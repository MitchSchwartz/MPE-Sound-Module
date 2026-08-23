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
