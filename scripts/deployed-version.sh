#!/usr/bin/env bash
# What is ACTUALLY running on this appliance?
#
# Written 2026-08-26 after a full day lost to a rollback that was believed
# deployed and was not. Two distinct failures happened that day and this
# reports both:
#   1. HEAD was not the commit we thought (a "revert" landed on a descendant
#      of the very commit it was meant to remove).
#   2. Services kept running pre-checkout code, because `git checkout` does
#      not reload a running Python process.
#
# Exit 0 = everything consistent. Exit 1 = something is stale or dirty.
set -uo pipefail

REPO="${MPE_MODULE_REPO:-/home/pi/MPE-Module}"
UNITS="${MPE_UNITS:-mpe-jackd mpe-sooperlooper mpe-looper-session touch-patch-browser mpe-peak-meter}"
rc=0

cd "$REPO" 2>/dev/null || { echo "deployed-version: FAIL — no repo at $REPO" >&2; exit 1; }

head_sha=$(git rev-parse --short HEAD 2>/dev/null)
head_sub=$(git log -1 --pretty=%s 2>/dev/null)
head_date=$(git log -1 --date=format:'%Y-%m-%d %H:%M' --pretty=%ad 2>/dev/null)
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')

echo "repo:   $REPO"
echo "commit: $head_sha  ($head_date)  $head_sub"
echo "branch: $branch"
if [ "$dirty" != "0" ]; then
    echo "tree:   DIRTY — $dirty uncommitted path(s); the running code is NOT this commit"
    rc=1
else
    echo "tree:   clean"
fi

# When did the working tree last change? A service started before this is stale.
checkout_epoch=$(stat -c %Y .git/HEAD 2>/dev/null || echo 0)
echo "checked out: $(date -d "@$checkout_epoch" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo unknown)"
echo

printf '%-24s %-9s %-21s %s\n' UNIT STATE STARTED CODE
for u in $UNITS; do
    state=$(systemctl is-active "$u" 2>/dev/null || true)
    ts=$(systemctl show "$u" -p ActiveEnterTimestamp --value 2>/dev/null)
    started_epoch=$(date -d "$ts" +%s 2>/dev/null || echo 0)
    if [ "$state" != "active" ]; then
        verdict="(not active)"; rc=1
    elif [ "$started_epoch" = "0" ] || [ "$checkout_epoch" = "0" ]; then
        verdict="unknown"
    elif [ "$started_epoch" -lt "$checkout_epoch" ]; then
        verdict="STALE — predates checkout, restart it"; rc=1
    else
        verdict="current"
    fi
    printf '%-24s %-9s %-21s %s\n' "$u" "${state:-?}" "${ts:-?}" "$verdict"
done

echo
if [ "$rc" = "0" ]; then
    echo "OK — every unit is running code from $head_sha."
else
    echo "MISMATCH — the running code is not $head_sha everywhere (see above)." >&2
fi
exit $rc
