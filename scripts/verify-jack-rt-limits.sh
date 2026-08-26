#!/usr/bin/env bash
# Verify jackd2 RT limits and audio group — non-zero ulimit -r required for trustworthy JACK.
# See docs/measurements/PROMPT-PI5-DAY0.md §1a (jackd2 debconf trap).
set -euo pipefail

fail=0
user="${1:-$(id -un)}"

if [ ! -f /etc/security/limits.d/audio.conf ]; then
    echo "FAIL: /etc/security/limits.d/audio.conf missing (jackd2 RT prompt likely answered no)" >&2
    echo "  Repair: sudo ./scripts/install-jack-audio-limits.sh" >&2
    fail=1
else
    echo "OK: /etc/security/limits.d/audio.conf present"
fi

if id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx audio; then
    echo "OK: user $user is in group audio"
else
    echo "FAIL: user $user not in group audio (sudo usermod -aG audio $user, re-login)" >&2
    fail=1
fi

rt_limit="$(su - "$user" -c 'ulimit -r' 2>/dev/null || ulimit -r)"
if [ "${rt_limit:-0}" -gt 0 ] 2>/dev/null; then
    echo "OK: ulimit -r = $rt_limit (for user $user)"
else
    echo "FAIL: ulimit -r is zero for $user — JACK runs without RT priority" >&2
    fail=1
fi

if command -v jackd >/dev/null 2>&1; then
    echo "OK: jackd $(jackd --version 2>&1 | head -1)"
else
    echo "FAIL: jackd not installed" >&2
    fail=1
fi

exit "$fail"
