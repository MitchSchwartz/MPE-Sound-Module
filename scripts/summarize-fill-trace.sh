#!/bin/bash
# Post-process mpe-fill-poller.sh output — wrap-safe fill_frames and sanity check.
#
# Usage:
#   ./scripts/summarize-fill-trace.sh FILL_LOG PERIOD NPERIODS
#
# Exits 1 if any fill_frames > period * nperiods (pointer arithmetic bug).

set -euo pipefail

LOG="${1:?fill log}"
PERIOD="${2:?period frames}"
NPERIODS="${3:?nperiods}"

BUF=$((PERIOD * NPERIODS))

awk -v buf="$BUF" '
    /^FILL_POLL_/ { next }
    NF >= 4 && $3 ~ /^[0-9]+$/ && $4 ~ /^[0-9]+$/ {
        appl = $3 + 0
        hw = $4 + 0
        fill = (appl - hw) % buf
        if (fill < 0) fill += buf
        n++
        s += fill
        vals[n] = fill
        if (n == 1 || fill < min) min = fill
        if (n == 1 || fill > max) max = fill
        if (fill > buf) {
            bad++
            if (bad <= 3) printf "SANITY_FAIL fill=%d > buf=%d appl=%d hw=%d\n", fill, buf, appl, hw > "/dev/stderr"
        }
    }
    END {
        if (n == 0) {
            print "FILL_SUMMARY n=0 buf=" buf
            exit 2
        }
        for (i = 1; i <= n; i++) {
            for (j = i + 1; j <= n; j++) {
                if (vals[i] > vals[j]) { t = vals[i]; vals[i] = vals[j]; vals[j] = t }
            }
        }
        p50 = vals[int((n + 1) / 2)]
        p1i = int(n * 0.01); if (p1i < 1) p1i = 1
        p1 = vals[p1i]
        p99i = int(n * 0.99); if (p99i < 1) p99i = 1; if (p99i > n) p99i = n
        p99 = vals[p99i]
        printf "FILL_SUMMARY n=%d buf=%d min=%d p1=%d p50=%d p99=%d max=%d mean=%.0f sanity_over_buf=%d\n", \
            n, buf, min, p1, p50, p99, max, s / n, bad + 0
        if (bad > 0) exit 1
    }
' "$LOG"
