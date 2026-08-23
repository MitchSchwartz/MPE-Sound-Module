#!/bin/bash
# PatchLoader reads MPE_POLY_* from child process env (not /etc/mpe/mpe.env).
mpe_as_user_init() {
    MPE_RUN_AS_USER="${MPE_RUN_AS_USER:-${MPE_PI_USER:-mitch}}"
    if [ -z "${MPE_RUN_AS_USER_HOME:-}" ]; then
        MPE_RUN_AS_USER_HOME="$(getent passwd "$MPE_RUN_AS_USER" | cut -d: -f6)"
    fi
}
mpe_as_user() {
    mpe_as_user_init
    sudo -u "$MPE_RUN_AS_USER" env \
        MPE_POLY_GOVERNOR="${MPE_POLY_GOVERNOR:-0}" \
        MPE_POLY_CEILING="${MPE_POLY_CEILING:-64}" \
        MPE_POLY_FLOOR="${MPE_POLY_FLOOR:-64}" \
        MPE_POLY_GOVERNOR_HEADROOM="${MPE_POLY_GOVERNOR_HEADROOM:-3}" \
        "$@"
}
mpe_load_patch_osc() {
    local patch_path="$1" script_dir="${2:?script_dir}"
    mpe_as_user python3 "${script_dir}/load-patch-osc.py" "$patch_path"
}
mpe_assert_poly_state_after_load() {
    local state_file="${MPE_RUN_AS_USER_HOME}/.patch_browser_poly_state.json"
    local native effective ceiling
    mpe_as_user_init
    if [ ! -f "$state_file" ]; then
        echo "ERROR: missing poly state after load: $state_file" >&2
        return 1
    fi
    native="$(grep -o '"native_poly"[[:space:]]*:[[:space:]]*[0-9]*' "$state_file" | grep -o '[0-9]*$' || true)"
    effective="$(grep -o '"effective_poly"[[:space:]]*:[[:space:]]*[0-9]*' "$state_file" | grep -o '[0-9]*$' || true)"
    ceiling="$(grep -o '"ceiling_poly"[[:space:]]*:[[:space:]]*[0-9]*' "$state_file" | grep -o '[0-9]*$' || true)"
    echo "PREFLIGHT poly_state native=${native:-?} effective=${effective:-?} ceiling=${ceiling:-?} file=${state_file}"
    if [ -z "$effective" ] || [ -z "$native" ]; then
        echo "ERROR: could not parse poly_state JSON" >&2
        return 1
    fi
    case "${MPE_POLY_GOVERNOR:-0}" in
        0|false|no|off)
            if [ "$effective" -lt "$native" ]; then
                echo "ERROR: effective_poly=${effective} < native_poly=${native} (load missing MPE_POLY_* in child env?)" >&2
                return 1
            fi
            if [ -n "$ceiling" ] && [ "$ceiling" -le 12 ] && [ "$native" -gt 12 ]; then
                echo "ERROR: ceiling_poly=${ceiling} touch-default with native=${native}" >&2
                return 1
            fi
            ;;
    esac
    return 0
}
mpe_preflight_dsp_spot_check() {
    local script_dir="$1" voices="$2" buffer="$3"
    local secs="${4:-45}" min_pct="${5:-50}"
    local raw="/tmp/mpe-preflight-dsp-$$.raw" load_log="/tmp/mpe-preflight-midi-$$.log"
    local med
    mpe_as_user_init
    : >"$raw"
    mpe_as_user python3 "${script_dir}/midi-load-hold.py" "$((secs + 5))" "$voices" >"$load_log" 2>&1 &
    local load_pid=$!
    sleep 2
    mpe_as_user stdbuf -oL jack_cpu_load >"$raw" 2>/dev/null &
    local jcl=$!
    sleep "$secs"
    kill -9 "$jcl" 2>/dev/null || true
    wait "$jcl" 2>/dev/null || true
    wait "$load_pid" 2>/dev/null || true
    med="$(awk '/[0-9]+\.[0-9]+/ { v = $NF + 0; if (v > 0 && v <= 200) a[++n] = v } END { if (n == 0) { print "0"; exit 1 }; for (i = 1; i <= n; i++) for (j = i + 1; j <= n; j++) if (a[i] > a[j]) { t = a[i]; a[i] = a[j]; a[j] = t }; printf "%.6f", a[int((n+1)/2)] }' "$raw")" || {
        echo "ERROR: preflight DSP spot-check: no jack_cpu_load samples" >&2
        return 1
    }
    echo "PREFLIGHT dsp_spot_check voices=${voices} buffer=${buffer} secs=${secs} dsp_median=${med} min_required=${min_pct}"
    awk -v v="$med" -v m="$min_pct" 'BEGIN { exit !(v+0 >= m+0) }' || {
        echo "ERROR: preflight dsp_median=${med}% below parity minimum ${min_pct}%" >&2
        return 1
    }
    return 0
}

mpe_query_surge_polylimit() {
    local script_dir="$1"
    mpe_as_user python3 "${script_dir}/manual/test-poly-governor-osc.py" 2>/dev/null \
        | sed -n 's/^Current polylimit.*: *//p' | head -1
}

mpe_assert_surge_polylimit_matches_state() {
    local script_dir="$1"
    local state_effective="$2"
    local live
    live="$(mpe_query_surge_polylimit "$script_dir")"
    echo "PREFLIGHT surge_polylimit_osc=${live:-?} state_effective=${state_effective:-?}"
    if [ -z "$live" ] || [ -z "$state_effective" ]; then
        echo "ERROR: could not read live polylimit or state effective_poly" >&2
        return 1
    fi
    if [ "$live" != "$state_effective" ]; then
        echo "ERROR: Surge OSC polylimit=${live} != state effective_poly=${state_effective}" >&2
        return 1
    fi
    return 0
}
