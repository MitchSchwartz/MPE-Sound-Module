#!/bin/bash
# Session control plane — structured event emit (Phase 2, spec criterion 9).
#
# Append-only JSONL ring buffer at $(mpe_run_dir)/events.jsonl. Bash callers
# source this after audio-engine.sh (for mpe_run_dir).
#
# JSON is built by scripts/mpe-session-event-emit.py (validated names, safe escaping).

# shellcheck disable=SC2034
MPE_SESSION_EVENTS_MAX="${MPE_SESSION_EVENTS_MAX:-2000}"

mpe_session_events_file() {
    printf '%s' "$(mpe_run_dir)/events.jsonl"
}

# Emit one line via Python (bash printf cannot safely escape reason= strings).
# Usage: mpe_session_event_append EVENT [DETAIL] [KEY=value ...]
mpe_session_event_append() {
    local event="${1:?event name required}"
    local detail="${2:-}"
    local source="${MPE_EVENT_SOURCE:-${0##*/}}"
    local repo script
    local -a cmd

    repo="${MPE_MODULE_REPO:-}"
    if [ -z "$repo" ]; then
        repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    fi
    script="${repo}/scripts/mpe-session-event-emit.py"
    if [ ! -f "$script" ]; then
        echo "mpe_session_event_append: missing $script" >&2
        return 1
    fi

    cmd=(python3 "$script" --source "$source")
    if [ -n "${MPE_RUN_DIR:-}" ]; then
        cmd+=(--run-dir "$MPE_RUN_DIR")
    fi
    if [ -n "$detail" ]; then
        cmd+=("$event" "$detail")
    else
        cmd+=("$event")
    fi
    shift 2 2>/dev/null || shift 1 2>/dev/null || true
    while [ "$#" -gt 0 ]; do
        case "$1" in
            *=*) cmd+=(--field "$1") ;;
        esac
        shift
    done
    "${cmd[@]}"
}
