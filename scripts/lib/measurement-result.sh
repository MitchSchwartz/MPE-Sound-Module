#!/bin/bash
# Strict parsers for measurement harness RESULT lines and physics checks.
# Source from harnesses and tests — never hand-roll grep for metric fields.
#
# Field name: dsp_median (NOT dsp_med — typo must hard-error).
set -uo pipefail

MPE_RESULT_STRICT="${MPE_RESULT_STRICT:-1}"

_mpe_result_die() {
    echo "ERROR: measurement-result: $*" >&2
    return 1
}

# Parse "key=value" tokens from a RESULT line into env vars MPE_R_<KEY>.
# Usage: mpe_result_parse_line "RESULT tag=foo xruns=3 dsp_median=19.5 ..."
mpe_result_parse_line() {
    local line="$1"
    local tok key val
    case "$line" in
        RESULT\ *) ;;
        *)
            _mpe_result_die "not a RESULT line: ${line:0:60}"
            return 1
            ;;
    esac
    if echo "$line" | grep -qE '(^|[[:space:]])dsp_med='; then
        _mpe_result_die "forbidden field dsp_med= (use dsp_median=)"
        return 1
    fi
    # shellcheck disable=SC2046
    for tok in $(echo "$line" | sed 's/^RESULT //'); do
        case "$tok" in
            *=*)
                key="${tok%%=*}"
                val="${tok#*=}"
                key="${key//[^a-zA-Z0-9_]/_}"
                printf -v "MPE_R_${key}" '%s' "$val"
                ;;
        esac
    done
    return 0
}

# Require named fields on the last parsed line (MPE_R_*).
mpe_result_require_fields() {
    local f
    for f in "$@"; do
        local var="MPE_R_${f}"
        if [ -z "${!var-}" ]; then
            _mpe_result_die "missing required field ${f}="
            return 1
        fi
        if [ "${!var}" = "?" ] || [ "${!var}" = "unknown" ]; then
            _mpe_result_die "field ${f}= is not a value (${!var})"
            return 1
        fi
        if [ "$f" = "dsp_median" ] && awk -v v="${!var}" "BEGIN{exit !(v+0==0)}"; then
            _mpe_result_die "field dsp_median=0 is not a measurement (sampler dead)"
            return 1
        fi
    done
    return 0
}

# Physics assertions on parsed primary row (MPE_R_*).
# Args: buffer_period [condition_label]
mpe_result_physics_assert() {
    local buf="${1:-}"
    local xr="${MPE_R_xruns-}"
    local dsp="${MPE_R_dsp_median-}"
    local samples="${MPE_R_samples-}"
    local jitter_n="${MPE_R_jitter_n-}"
    local live="${MPE_R_meter_live-}"

    if [ -n "$live" ] && [ "$live" != "1" ]; then
        _mpe_result_die "meter_live=${live} (expected 1)"
        return 1
    fi

    if [ -n "$samples" ] && [ -n "${MPE_EXPECT_SAMPLES-}" ]; then
        if [ "$samples" != "$MPE_EXPECT_SAMPLES" ]; then
            _mpe_result_die "samples=${samples} expected ${MPE_EXPECT_SAMPLES}"
            return 1
        fi
    fi

    if [ -n "$jitter_n" ] && [ -n "${MPE_EXPECT_SAMPLES-}" ] && [ "$MPE_EXPECT_SAMPLES" -ge 30 ]; then
        if [ "$jitter_n" -lt 100 ] 2>/dev/null; then
            _mpe_result_die "jitter_n=${jitter_n} too low for ${MPE_EXPECT_SAMPLES}s window"
            return 1
        fi
    fi

    if [ -n "$xr" ] && [ -n "$dsp" ]; then
        # 10% DSP with material xruns at 512 is impossible (baseline ~38%)
        if [ "$buf" = "512" ] || [[ "${MPE_R_tag-}" = *"-b512-"* ]]; then
            if awk -v d="$dsp" -v x="$xr" 'BEGIN { exit !(d+0 < 15 && x+0 > 5) }'; then
                _mpe_result_die "physics: dsp_median=${dsp}% with xruns=${xr} at 512 impossible"
                return 1
            fi
        fi
    fi
    return 0
}

# Compare two dsp_median values across buffer halving — >50% drop is impossible.
mpe_result_physics_buffer_halving() {
    local dsp_large="$1"
    local dsp_small="$2"
    if awk -v a="$dsp_large" -v b="$dsp_small" '
        BEGIN {
            if (a+0 <= 0) exit 1
            drop = (a - b) / a * 100
            if (drop > 50) exit 0
            exit 1
        }'; then
        _mpe_result_die "physics: dsp ${dsp_large}% -> ${dsp_small}% when halving buffer (>50% drop impossible)"
        return 1
    fi
    return 0
}

# Parse all RESULT rows for a tag from a log file (fields span multiple lines).
mpe_result_load_tag() {
    local file="$1"
    local tag="$2"
    local line
    unset MPE_R_tag MPE_R_xruns MPE_R_dsp_median MPE_R_dsp_p99 MPE_R_dsp_max
    unset MPE_R_meter_live MPE_R_meter_max_age_s MPE_R_samples MPE_R_jitter_n
    unset MPE_R_window_align
    if ! grep -qE "^RESULT tag=${tag} " "$file"; then
        _mpe_result_die "no RESULT for tag=${tag} in ${file}"
        return 1
    fi
    local primary
    primary="$(grep -E "^RESULT tag=${tag} xruns=" "$file" | head -1)" || {
        _mpe_result_die "no primary RESULT row for tag=${tag}"
        return 1
    }
    mpe_result_parse_line "$primary" || return 1
    while IFS= read -r line; do
        case "$line" in
            RESULT\ tag=${tag}\ samples=*|RESULT\ tag=${tag}\ jitter_*|RESULT\ tag=${tag}\ frames_*)
                mpe_result_parse_line "$line" || return 1
                ;;
        esac
    done < <(grep -E "^RESULT tag=${tag} " "$file")
    mpe_result_require_fields xruns meter_live dsp_median dsp_p99 dsp_max samples || return 1
    return 0
}

# Attempt V11-style recovery: emit xruns table; withhold DSP when dsp_med or missing median.
mpe_result_v11_recover() {
    local file="$1"
    local out="${2:-}"
    local line tag xr dsp withhold
    [ -r "$file" ] || { _mpe_result_die "v11 recover: missing ${file}"; return 1; }
    while IFS= read -r line; do
        withhold=0
        case "$line" in
            RESULT\ tag=*xruns=*)
                if echo "$line" | grep -qE '(^|[[:space:]])dsp_med='; then
                    withhold=1
                fi
                tag="${line#*tag=}"
                tag="${tag%% *}"
                xr="${line#*xruns=}"
                xr="${xr%% *}"
                dsp=""
                if echo "$line" | grep -q 'dsp_median='; then
                    dsp="${line#*dsp_median=}"
                    dsp="${dsp%% *}"
                else
                    withhold=1
                fi
                printf 'tag=%s xruns=%s dsp_median=%s dsp_withheld=%s\n' \
                    "$tag" "$xr" "${dsp:-WITHHELD}" "$withhold"
                ;;
        esac
    done <"$file" >"${out:-/dev/stdout}"
    return 0
}
