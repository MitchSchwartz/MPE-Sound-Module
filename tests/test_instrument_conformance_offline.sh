#!/bin/bash
# C0 — offline parser/fixture conformance (reader, not live instrument).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="${ROOT}/tests/fixtures/instrument-conformance"
# shellcheck source=../scripts/lib/measurement-result.sh
source "${ROOT}/scripts/lib/measurement-result.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }


# --- Derived floors: monotonic 1024 < 512 < 256 (V9/W1 anchors) ---
mpe_result_assert_floor_monotonic || fail "plausibility floors not monotonic"
[ "$(mpe_result_dsp_plausibility_floor 1024)" = "7.6" ] || fail "1024 floor from V9 38.0%"
[ "$(mpe_result_dsp_plausibility_floor 512)" = "12.5" ] || fail "512 floor from W1 62.4%"
[ "$(mpe_result_dsp_plausibility_floor 256)" = "15.2" ] || fail "256 floor from W1 76.1%"
awk -v a="$(mpe_result_dsp_plausibility_floor 1024)" -v b="$(mpe_result_dsp_plausibility_floor 512)" -v c="$(mpe_result_dsp_plausibility_floor 256)"     'BEGIN{exit !(a+0 < b+0 && b+0 < c+0)}' || fail "floor ordering 1024<512<256"
ok "plausibility floors monotonic and V9/W1-derived"

# --- Positive: full harness path (load_tag + physics, tag-derived buffer) ---
MPE_EXPECT_SAMPLES=60
mpe_result_assert_tag "${FIX}/good-512-a.log" "A-b512-p3-l0-run1" || fail "harness path good 512"
[ "${MPE_R_xruns}" = "2" ] || fail "xruns positive"
[ "${MPE_R_dsp_median}" = "38.520000" ] || fail "dsp_median positive"
ok "harness-path assert_tag good 512"

# --- F2: tag fallback must work without hand-passed buffer ---
mpe_result_parse_line "$(grep '^RESULT' "${FIX}/physics-low-dsp-high-xr.log" | head -1)"
MPE_R_tag="A-b512-p3-l0-run1"
if mpe_result_physics_assert "" 2>/dev/null; then
    fail "physics should reject via tag-derived buffer 512"
fi
ok "physics: tag fallback -b512- (F2)"

# --- F3: V11 256×3 impossible cell (10% DSP, 23 xruns) ---
mpe_result_parse_line "$(grep '^RESULT' "${FIX}/physics-256-v11.log" | head -1)"
MPE_R_tag="A-b256-p3-l0-run1"
if mpe_result_physics_assert "" 2>/dev/null; then
    fail "physics should reject 256 V11 impossible cell"
fi
ok "physics: 256×3 low DSP + material xruns (F3)"

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

# --- F4: idle 0.9% at 256 fails plausibility floor ---
mpe_result_parse_line "RESULT tag=A-b256-p3-l0-run1 xruns=0 meter_live=1 dsp_median=0.900000 dsp_p99=1.000000 dsp_max=1.100000 samples=60"
if mpe_result_require_fields dsp_median 2>/dev/null; then
    fail "0.9% at 256 should fail plausibility floor (F4)"
fi
ok "plausibility floor rejects V11 idle 0.9% at 256 (F4)"

# --- F5: non-numeric jitter_n halts ---
mpe_result_parse_line "$(grep '^RESULT' "${FIX}/good-512-a.log" | grep 'jitter_n=' | head -1)"
MPE_R_jitter_n="not-a-number"
MPE_EXPECT_SAMPLES=60
if mpe_result_physics_assert 512 2>/dev/null; then
    fail "non-numeric jitter_n should halt (F5)"
fi
ok "jitter_n numeric guard (F5)"

# --- Physics: buffer halving 39.6 -> 1.6 impossible ---
if mpe_result_physics_buffer_halving 39.6 1.6 2>/dev/null; then
    fail "physics should reject 39.6% -> 1.6% drop"
fi
ok "physics: buffer-halving DSP collapse rejected"

mpe_result_physics_buffer_halving 19.14 38.52 || fail "19->38 should pass halving check"
ok "physics: plausible DSP increase passes"

# --- V11 recovery ---
v11_out="$(mpe_result_v11_recover "${FIX}/dsp-med-typo.log" /dev/stdout)"
echo "$v11_out" | grep -q 'xruns=23' || fail "V11 xruns column"
echo "$v11_out" | grep -q 'dsp_withheld=1' || fail "V11 DSP withheld on dsp_med"
ok "V11 recovery withholds DSP on typo"

if mpe_result_v11_recover "${FIX}/no-such.log" 2>/dev/null; then
    fail "v11 missing file should halt"
fi
ok "v11 missing file halts"

grep -q 'cat "$OUT"' "${ROOT}/scripts/xrun-corr.sh" || fail "xrun-corr must emit OUT on stdout"
ok "xrun-corr stdout (occurrence #1)"

# --- window_align on good fixture ---
mpe_result_load_tag "${FIX}/good-512-a.log" "A-b512-p3-l0-run1" || fail "reload good 512"
[ "${MPE_R_window_align}" = "1" ] || fail "window_align positive"
ok "window_align on primary row"

# --- V11 recovery: good row not withheld ---
v11_good="$(mpe_result_v11_recover "${FIX}/good-512-a.log" /dev/stdout)"
echo "$v11_good" | grep -q 'dsp_median=38.520000' || fail "V11 good dsp"
echo "$v11_good" | grep -q 'dsp_withheld=0' || fail "V11 good not withheld"
ok "V11 recovery keeps good DSP"

# --- V11 withhold per-row, not sticky ---
tmp="$(mktemp)"
printf '%s\n' \
    'RESULT tag=bad xruns=1 dsp_med=1' \
    'RESULT tag=good xruns=0 dsp_median=19.0' >"$tmp"
out="$(mpe_result_v11_recover "$tmp" /dev/stdout)"
echo "$out" | grep 'tag=good' | grep -q 'dsp_withheld=0' || fail "V11 order: good row not withheld"
echo "$out" | grep 'tag=bad' | grep -q 'dsp_withheld=1' || fail "V11 order: bad row withheld"
rm -f "$tmp"
ok "V11 withhold per-row not sticky"

# --- xrun-corr fixture TOTAL line ---
if ! grep -q '^TOTAL .* meter_live=1' "${FIX}/xrun-corr-good.out"; then
    fail "xrun-corr fixture missing TOTAL meter_live=1"
fi
ok "xrun-corr TOTAL positive fixture"

# --- S2: physics VOID when tag has no buffer token ---
mpe_result_parse_line "RESULT tag=nobuffer xruns=23 meter_live=1 dsp_median=1.600000 dsp_p99=2 dsp_max=3 samples=60"
if mpe_result_physics_assert "" 2>/dev/null; then
    fail "physics should VOID when buffer unresolvable"
fi
ok "physics VOID without -bNNN- tag (S2)"

# --- S3: plausibility VOID when samples != MPE_EXPECT_SAMPLES (truncated window) ---
MPE_EXPECT_SAMPLES=60
mpe_result_parse_line "RESULT tag=A-b256-p3-l0-run1 xruns=0 meter_live=1 dsp_median=0.900000 samples=12"
if mpe_result_require_fields dsp_median 2>/dev/null; then
    fail "samples=12 with EXPECT=60 should VOID plausibility check"
fi
unset MPE_EXPECT_SAMPLES
ok "plausibility VOID on samples mismatch (S3)"

# --- S7: non-numeric xruns VOID physics ---
mpe_result_parse_line "RESULT tag=A-b512-p3-l0-run1 xruns=notnum meter_live=1 dsp_median=10.0 samples=60"
if mpe_result_physics_assert 512 2>/dev/null; then
    fail "non-numeric xruns should VOID physics"
fi
ok "physics VOID on non-numeric xruns (S7)"

# --- S8: non-numeric jitter_n always VOID ---
mpe_result_parse_line "RESULT tag=A-b512-p3-l0-run1 jitter_n=not-a-number"
unset MPE_EXPECT_SAMPLES
if mpe_result_physics_assert 512 2>/dev/null; then
    fail "non-numeric jitter_n should VOID without MPE_EXPECT_SAMPLES"
fi
ok "jitter_n numeric guard without EXPECT (S8)"

# --- jack_cpu_load raw capture (Pi stdbuf path) ---
tmp="$(mktemp)"
printf '%s\n' \
    'jack DSP load 9.729879' \
    'jack DSP load 9.711891' \
    'jack DSP load 9.640977' >"$tmp"
med="$(mpe_result_jack_cpu_load_median "$tmp")"
rm -f "$tmp"
[ "$med" = "9.711891" ] || fail "raw jack_cpu_load median got ${med}"
ok "jack_cpu_load median parses raw DSP load lines"

echo "test_instrument_conformance_offline.sh: all checks passed"
