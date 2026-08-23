#!/bin/bash
# Strict parsers for measurement harness RESULT lines and physics checks.
# Source from harnesses and tests — never hand-roll grep for metric fields.
#
# Field name: dsp_median (NOT dsp_med — typo must hard-error).
set -uo pipefail

MPE_RESULT_STRICT="${MPE_RESULT_STRICT:-1}"

# Plausibility floors: 20% of minimum confirmed *loaded* dsp_median (V9/W1).
# Same absolute work → higher % as buffer shrinks → floors increase 1024 < 512 < 256.
#   1024: V9 Duduk @3 — 38.0% (docs/measurements/v9-probe-duration-2026-08-22.md)
#   512:  W1-b 512×3 — 62.4% (docs/measurements/w1-instrumented-window-2026-08-21.md)
#   256:  W1-c 256×3 — 76.1% (docs/measurements/w1-instrumented-window-2026-08-21.md)
# V11 idle/mistimed signatures (0.9%, 1.6%) sit far below these floors.
if [ -z "${_MPE_MEASUREMENT_RESULT_SOURCED:-}" ]; then
    readonly MPE_DSP_FLOOR_1024="7.6"
    readonly MPE_DSP_FLOOR_512="12.5"
    readonly MPE_DSP_FLOOR_256="15.2"
    _MPE_MEASUREMENT_RESULT_SOURCED=1
fi

_mpe_result_die() {
    echo "ERROR: measurement-result: $*" >&2
    return 1
}

mpe_result_reset() {
    local v
    for v in $(compgen -v MPE_R_ 2>/dev/null || true); do
        unset "$v"
    done
}

mpe_result_buffer_from_tag() {
    local tag="${1:-${MPE_R_tag-}}"
    if [[ "$tag" =~ -b([0-9]+)- ]]; then
        echo "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

mpe_result_dsp_plausibility_floor() {
    local buf="${1:-}"
    case "$buf" in
        1024) echo "$MPE_DSP_FLOOR_1024" ;;
        512) echo "$MPE_DSP_FLOOR_512" ;;
        256) echo "$MPE_DSP_FLOOR_256" ;;
        *)
            _mpe_result_die "unknown buffer ${buf} for plausibility floor"
            return 1
            ;;
    esac
}

# Assert floor(1024) < floor(512) < floor(256). Called from offline tests.
mpe_result_assert_floor_monotonic() {
    awk -v a="$MPE_DSP_FLOOR_1024" -v b="$MPE_DSP_FLOOR_512" -v c="$MPE_DSP_FLOOR_256" \
        'BEGIN { if (a+0 < b+0 && b+0 < c+0) exit 0; exit 1 }' || {
        _mpe_result_die "plausibility floors not monotonic: 1024=${MPE_DSP_FLOOR_1024} 512=${MPE_DSP_FLOOR_512} 256=${MPE_DSP_FLOOR_256}"
        return 1
    }
    return 0
}

_mpe_result_physics_low_dsp_ceiling() {
    local buf="${1:-}"
    case "$buf" in
        1024) echo "10" ;;
        512) echo "15" ;;
        256) echo "20" ;;
        *)
            _mpe_result_die "unknown buffer ${buf} for physics ceiling"
            return 1
            ;;
    esac
}

_mpe_result_resolve_buffer() {
    local buf="${1:-}"
    if [ -n "$buf" ]; then
        echo "$buf"
        return 0
    fi
    mpe_result_buffer_from_tag "${MPE_R_tag-}"
}

# Median of jack_cpu_load samples in a capture file.
# Accepts formatted run rows (measure-latency-run) or raw "jack DSP load N.NNNNNN" lines.
mpe_result_jack_cpu_load_median() {
    local run_file="$1"
    awk '
        function take(v) {
            if (v != "?" && v+0 > 0 && v+0 <= 200) { a[++n]=v+0 }
        }
        /^[[:space:]]+[0-9]+/ {
            take($2)
        }
        /^jack DSP load / {
            take($NF)
        }
        END {
            if (n==0) { exit 1 }
            for (i=1;i<=n;i++) {
                for (j=i+1;j<=n;j++) if (a[i]>a[j]) { t=a[i]; a[i]=a[j]; a[j]=t }
            }
            med=a[int((n+1)/2)]
            printf "%.6f\n", med
        }
    ' "$run_file"
}

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
        if [ "$f" = "dsp_median" ]; then
            if awk -v v="${!var}" "BEGIN{exit !(v+0==0)}"; then
                _mpe_result_die "field dsp_median=0 is not a measurement (sampler dead)"
                return 1
            fi
            local buf floor
            if ! buf="$(_mpe_result_resolve_buffer "")"; then
                _mpe_result_die "dsp plausibility: cannot resolve buffer from tag"
                return 1
            fi
            if [ -z "${MPE_R_samples-}" ] || ! [[ "${MPE_R_samples}" =~ ^[0-9]+$ ]]; then
                _mpe_result_die "dsp plausibility: missing or non-numeric samples="
                return 1
            fi
            if [ -n "${MPE_EXPECT_SAMPLES-}" ]; then
                if [ "${MPE_R_samples}" != "$MPE_EXPECT_SAMPLES" ]; then
                    _mpe_result_die "dsp plausibility: samples=${MPE_R_samples} expected ${MPE_EXPECT_SAMPLES}"
                    return 1
                fi
            fi
            floor="$(mpe_result_dsp_plausibility_floor "$buf")" || return 1
            if awk -v v="${!var}" -v fl="$floor" 'BEGIN{exit !(v+0 < fl+0)}'; then
                _mpe_result_die "field dsp_median=${!var}% below plausibility floor ${floor}% at buffer ${buf}"
                return 1
            fi
        fi
    done
    return 0
}

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

    if [ -n "$jitter_n" ]; then
        if ! [[ "$jitter_n" =~ ^[0-9]+$ ]]; then
            _mpe_result_die "jitter_n=${jitter_n} is not numeric"
            return 1
        fi
        if [ -n "${MPE_EXPECT_SAMPLES-}" ] && [ "$MPE_EXPECT_SAMPLES" -ge 30 ] && [ "$jitter_n" -lt 100 ]; then
            _mpe_result_die "jitter_n=${jitter_n} too low for ${MPE_EXPECT_SAMPLES}s window"
            return 1
        fi
    fi

    if [ -n "$xr" ] && [ -n "$dsp" ]; then
        if ! [[ "$xr" =~ ^[0-9]+$ ]]; then
            _mpe_result_die "xruns=${xr} is not numeric"
            return 1
        fi
        if ! buf="$(_mpe_result_resolve_buffer "$buf")"; then
            _mpe_result_die "physics: cannot resolve buffer from tag or argument"
            return 1
        fi
        local ceiling
        ceiling="$(_mpe_result_physics_low_dsp_ceiling "$buf")" || return 1
        if awk -v d="$dsp" -v x="$xr" -v c="$ceiling" 'BEGIN { exit !(x+0 > 5 && d+0 < c+0) }'; then
            _mpe_result_die "physics: dsp_median=${dsp}% with xruns=${xr} at buffer=${buf} impossible (low DSP + material xruns)"
            return 1
        fi
    fi
    return 0
}

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

mpe_result_assert_tag() {
    local file="$1"
    local tag="$2"
    local buf
    mpe_result_load_tag "$file" "$tag" || return 1
    buf="$(_mpe_result_resolve_buffer "")" || return 1
    mpe_result_physics_assert "$buf" || return 1
    return 0
}

mpe_result_load_tag() {
    local file="$1"
    local tag="$2"
    local line
    mpe_result_reset
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
