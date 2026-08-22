#!/bin/bash
# C0 — offline instrument conformance tests (positive, negative, physics per metric).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="${ROOT}/tests/fixtures/instrument-conformance"
# shellcheck source=../scripts/lib/measurement-result.sh
source "${ROOT}/scripts/lib/measurement-result.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

# --- Positive: dsp_median parses from good fixture ---
MPE_EXPECT_SAMPLES=60
mpe_result_load_tag "${FIX}/good-512-a.log" "A-b512-p3-l0-run1" || fail "load good 512"
[ "${MPE_R_xruns}" = "2" ] || fail "xruns positive"
[ "${MPE_R_dsp_median}" = "38.520000" ] || fail "dsp_median positive"
[ "${MPE_R_meter_live}" = "1" ] || fail "meter_live positive"
[ "${MPE_R_samples}" = "60" ] || fail "samples positive"
[ "${MPE_R_window_align}" = "1" ] || fail "window_align positive"
ok "positive controls on primary RESULT row"

# --- Negative: dsp_med typo must halt ---
if mpe_result_parse_line "$(grep '^RESULT' "${FIX}/dsp-med-typo.log" | head -1)" 2>/dev/null; then
    fail "dsp_med= should hard-error"
fi
ok "negative control dsp_med= halts"

# --- Negative: missing dsp_median ---
unset MPE_R_dsp_median
MPE_R_xruns=1
MPE_R_meter_live=1
if mpe_result_require_fields xruns dsp_median 2>/dev/null; then
    fail "missing dsp_median should halt"
fi
ok "negative control missing dsp_median halts"

# --- Physics: 10% DSP + 23 xruns at 512 impossible ---
mpe_result_parse_line "$(grep '^RESULT' "${FIX}/physics-low-dsp-high-xr.log" | head -1)"
export MPE_R_xruns MPE_R_dsp_median MPE_R_tag="A-b512-p3-l0-run1"
if mpe_result_physics_assert 512 2>/dev/null; then
    fail "physics should reject low dsp + high xruns at 512"
fi
ok "physics: 10% DSP with 23 xruns rejected"

# --- Physics: buffer halving 39.6 -> 1.6 impossible ---
if mpe_result_physics_buffer_halving 39.6 1.6 2>/dev/null; then
    fail "physics should reject 39.6% -> 1.6% drop"
fi
ok "physics: buffer-halving DSP collapse rejected"

# --- Physics: buffer halving modest change passes ---
mpe_result_physics_buffer_halving 19.14 38.52 || fail "19->38 should pass halving check"
ok "physics: plausible DSP increase passes"

# --- V11 recovery: xruns stand, DSP withheld on typo log ---
v11_out="$(mpe_result_v11_recover "${FIX}/dsp-med-typo.log" /dev/stdout)"
echo "$v11_out" | grep -q 'xruns=23' || fail "V11 xruns column"
echo "$v11_out" | grep -q 'dsp_withheld=1' || fail "V11 DSP withheld on dsp_med"
ok "V11 recovery withholds DSP on typo"

v11_good="$(mpe_result_v11_recover "${FIX}/good-512-a.log" /dev/stdout)"
echo "$v11_good" | grep -q 'dsp_median=38.520000' || fail "V11 good dsp"
echo "$v11_good" | grep -q 'dsp_withheld=0' || fail "V11 good not withheld"
ok "V11 recovery keeps good DSP"

if mpe_result_v11_recover "${FIX}/no-such.log" 2>/dev/null; then
    fail "v11 missing file should halt"
fi
ok "v11 missing file halts"


# --- xrun-corr: TOTAL line positive / missing TOTAL negative ---
if ! grep -q '^TOTAL .* meter_live=1' "${FIX}/xrun-corr-good.out"; then
    fail "xrun-corr fixture missing TOTAL meter_live=1"
fi
if grep -q '^TOTAL ' /dev/null 2>/dev/null; then :; fi
if grep -q '^TOTAL ' "${FIX}/missing-corr.out" 2>/dev/null; then
    fail "missing-corr should not exist with TOTAL"
fi
grep -q 'cat "$OUT"' "${ROOT}/scripts/xrun-corr.sh" || fail "xrun-corr must emit OUT on stdout"
ok "xrun-corr TOTAL positive control"


# --- V11 withhold is per-row, not sticky ---
tmp="$(mktemp)"
printf '%s
'   'RESULT tag=bad xruns=1 dsp_med=1'   'RESULT tag=good xruns=0 dsp_median=19.0' >"$tmp"
out="$(mpe_result_v11_recover "$tmp" /dev/stdout)"
echo "$out" | grep 'tag=good' | grep -q 'dsp_withheld=0' || fail "V11 order: good row not withheld"
echo "$out" | grep 'tag=bad' | grep -q 'dsp_withheld=1' || fail "V11 order: bad row withheld"
rm -f "$tmp"
ok "V11 withhold per-row not sticky"

echo "test_instrument_conformance.sh: all checks passed"
