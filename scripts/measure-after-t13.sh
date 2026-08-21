#!/bin/bash
# Wait for T13, pull latest harness, run T12 then T7a.
#
# Usage: nohup bash ~/MPE-Module/scripts/measure-after-t13.sh &
#
set -euo pipefail

REPO="${MPE_MODULE_REPO:-$HOME/MPE-Module}"
T13_DONE="${HOME}/t13-condA.nohup"

echo "=== measure-after-t13 waiting for T13 $(date -Is) ==="

while ! grep -q 'SENTINEL t13-complete' "$T13_DONE" 2>/dev/null; do
    sleep 30
done

echo "T13 complete — pulling plan/t7-sequence $(date -Is)"
cd "$REPO"
git pull origin plan/t7-sequence

echo "=== starting T12 $(date -Is) ==="
sudo "$REPO/scripts/measure-t12.sh" --output "${HOME}/t12-condA.log"

echo "=== starting T7a $(date -Is) ==="
sudo "$REPO/scripts/measure-t7a.sh" --output "${HOME}/t7a-periods.log"

echo "SENTINEL remaining-tests-complete $(date -Is)"
