#!/bin/bash
# Session control plane — structured event emit (Phase 2, spec criterion 9).
#
# Append-only JSONL ring buffer at $(mpe_run_dir)/events.jsonl. Bash callers
# source this after audio-engine.sh (for mpe_run_dir).

# shellcheck disable=SC2034
MPE_SESSION_EVENTS_MAX="${MPE_SESSION_EVENTS_MAX:-2000}"

mpe_session_events_file() {
    printf '%s' "$(mpe_run_dir)/events.jsonl"
}

# Emit one line: {"ts":...,"event":"...","source":"...","detail":"..."}
# Extra keys may be passed as KEY=value pairs after detail.
mpe_session_event_append() {
    local event="${1:?event name required}"
    local detail="${2:-}"
    local source="${MPE_EVENT_SOURCE:-${0##*/}}"
    local file tmp line ts
    file="$(mpe_session_events_file)"
    ts="$(date +%s)"
    line="$(printf '{"ts":%s,"event":"%s","source":"%s"' "$ts" "$event" "$source")"
    if [ -n "$detail" ]; then
        # shellcheck disable=SC2016
        line="${line},$(printf '"detail":"%s"' "$detail")"
    fi
    shift 2 || shift 1 || true
    while [ "$#" -gt 0 ]; do
        case "$1" in
            *=*)
                local k="${1%%=*}" v="${1#*=}"
                line="${line},$(printf '"%s":"%s"' "$k" "$v")"
                ;;
        esac
        shift
    done
    line="${line}}"

    mkdir -p "$(dirname "$file")" 2>/dev/null || true
    tmp="${file}.tmp.$$"
    if [ -f "$file" ]; then
        local count
        count="$(wc -l <"$file" 2>/dev/null || echo 0)"
        if [ "$count" -ge "$MPE_SESSION_EVENTS_MAX" ]; then
            tail -n "$((MPE_SESSION_EVENTS_MAX - 1))" "$file" >"$tmp" 2>/dev/null || : >"$tmp"
        else
            cp "$file" "$tmp" 2>/dev/null || : >"$tmp"
        fi
    else
        : >"$tmp"
    fi
    printf '%s\n' "$line" >>"$tmp"
    chmod 0644 "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$file" 2>/dev/null || {
        rm -f "$tmp" 2>/dev/null || true
        return 1
    }
}
