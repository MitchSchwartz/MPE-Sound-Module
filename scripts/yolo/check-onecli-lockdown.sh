#!/usr/bin/env bash
# Gate: YOLO agents cannot reach OneCLI admin (CLI, onecli-nerdrack, port 10254 API, queue approve).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

export YOLO_HOOK_AGENT=claude
GUARD="$ROOT/scripts/yolo/yolo-shell-guard.sh"

probe_deny() {
  local label="$1"
  local cmd="$2"
  local payload
  payload=$(printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":%s}}' \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$cmd")")
  local result
  result=$(echo "$payload" | bash "$GUARD")
  if echo "$result" | grep -qE '"permissionDecision"[[:space:]]*:[[:space:]]*"deny"'; then
    echo "  deny probe ok: ${label}"
    return 0
  fi
  echo "FAIL: yolo-shell-guard should deny: ${label}" >&2
  echo "  command: ${cmd}" >&2
  echo "  got: ${result}" >&2
  return 1
}

probe_allow() {
  local label="$1"
  local cmd="$2"
  local payload
  payload=$(printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":%s}}' \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$cmd")")
  local result
  result=$(echo "$payload" | bash "$GUARD")
  if echo "$result" | grep -qE '"permissionDecision"[[:space:]]*:[[:space:]]*"allow"'; then
    echo "  allow probe ok: ${label}"
    return 0
  fi
  echo "FAIL: yolo-shell-guard should allow: ${label}" >&2
  echo "  command: ${cmd}" >&2
  echo "  got: ${result}" >&2
  return 1
}

echo "== check-onecli-lockdown =="
probe_deny "onecli CLI" "onecli agents list"
probe_deny "onecli-nerdrack script" "onecli-nerdrack setup-mpe --from-gh"
probe_deny "OneCLI admin API POST" "curl -sS -X POST http://127.0.0.1:10254/api/agents -d '{}'"
probe_deny "OneCLI admin API GET" "curl -sS http://127.0.0.1:10254/api/secrets"
probe_deny "queue approve" "bash scripts/yolo/enqueue-yolo-task.sh approve --id smoke"
probe_allow "git status" "git status"
probe_allow "unit tests" "python3 -m unittest discover -s tests -q"
echo "All OneCLI lockdown gates passed."
