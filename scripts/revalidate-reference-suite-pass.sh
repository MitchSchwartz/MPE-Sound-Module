#!/bin/bash
# Offline re-validation of reference-suite pass logs against current parser.
#
# Rule 0 cheap check: re-run mpe_result_load_tag + mpe_result_physics_assert on
# each loaded cell's primary tag without Pi measurement time.
#
# Usage:
#   ./scripts/revalidate-reference-suite-pass.sh ARTIFACT_DIR [SECONDS_HOLD]
#   ./scripts/revalidate-reference-suite-pass.sh ~/reference-suite-pi4-20260822-204559 25
#
# Exit 0 only if every cell-P*.log passes. Silence cells (S*) are skipped.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/measurement-result.sh
source "$SCRIPT_DIR/lib/measurement-result.sh"

ARTIFACT_DIR="${1:?artifact dir required}"
SECONDS_HOLD="${2:-25}"

export MPE_EXPECT_SAMPLES="$SECONDS_HOLD"
JITTER_FLOOR="$(mpe_result_jitter_n_floor "$SECONDS_HOLD")"
PARSER_COMMIT="$(git -C "$(dirname "$SCRIPT_DIR")" rev-parse --short HEAD 2>/dev/null || echo unknown)"

pass=0
fail=0
results=()

for f in "$ARTIFACT_DIR"/cell-P*.log; do
    [ -f "$f" ] || continue
    cell="$(basename "$f" .log | sed 's/cell-//;s/-b[0-9].*//')"
    tag="$(grep '^SENTINEL run-complete' "$f" | tail -1 | sed 's/.*tag=//;s/ xruns=.*//')"
    mpe_result_reset
    err=""
    if mpe_result_load_tag "$f" "$tag" 2>/tmp/revalidate-err.$$; then
        if ! mpe_result_physics_assert "" 2>/tmp/revalidate-err.$$; then
            err="$(tail -1 /tmp/revalidate-err.$$)"
            status=FAIL
            fail=$((fail + 1))
        elif [ -n "${MPE_R_jitter_n:-}" ] && [ "${MPE_R_jitter_n}" -lt "$JITTER_FLOOR" ]; then
            err="jitter_n=${MPE_R_jitter_n} below floor ${JITTER_FLOOR}"
            status=FAIL
            fail=$((fail + 1))
        else
            status=PASS
            pass=$((pass + 1))
        fi
    else
        err="$(tail -1 /tmp/revalidate-err.$$)"
        status=FAIL
        fail=$((fail + 1))
    fi
    results+=("${cell}	${tag}	${status}	${MPE_R_xruns:-?}	${MPE_R_dsp_median:-?}	${MPE_R_samples:-?}	${MPE_R_jitter_n:-?}	${err}")
    printf '%-20s %-22s %-6s xruns=%-4s dsp=%-10s samples=%-3s jitter_n=%-6s %s\n' \
        "$cell" "$tag" "$status" "${MPE_R_xruns:-?}" "${MPE_R_dsp_median:-?}" \
        "${MPE_R_samples:-?}" "${MPE_R_jitter_n:-?}" "$err"
done
rm -f /tmp/revalidate-err.$$

echo "---"
echo "PASS=${pass} FAIL=${fail} jitter_floor=${JITTER_FLOOR} MPE_EXPECT_SAMPLES=${MPE_EXPECT_SAMPLES} parser=${PARSER_COMMIT}"

NOTE="${ARTIFACT_DIR}/revalidation-${PARSER_COMMIT}-$(date +%Y%m%d).md"
{
    echo "# Reference suite pass re-validation"
    echo ""
    echo "- **Date:** $(date -Is)"
    echo "- **Parser commit:** ${PARSER_COMMIT}"
    echo "- **MPE_EXPECT_SAMPLES:** ${MPE_EXPECT_SAMPLES}"
    echo "- **jitter_n floor:** ${JITTER_FLOOR}"
    echo "- **Artifact dir:** ${ARTIFACT_DIR}"
    echo "- **Verdict:** $([ "$fail" -eq 0 ] && echo CONTROL STANDS || echo RE-RUN REQUIRED)"
    echo ""
    echo "| cell | tag | status | xruns | dsp_median | samples | jitter_n | note |"
    echo "|------|-----|--------|-------|------------|---------|----------|------|"
    for row in "${results[@]}"; do
        IFS=$'\t' read -r c t s x d sm jn n <<<"$row"
        echo "| ${c} | ${t} | ${s} | ${x} | ${d} | ${sm} | ${jn} | ${n} |"
    done
} >"$NOTE"
echo "Wrote ${NOTE}"

[ "$fail" -eq 0 ]
