#!/usr/bin/env bash
# Gate: agentjail + yolo-shell-guard before Claude YOLO sessions on nerdrack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

source "${HOME}/.agentjail/env" 2>/dev/null || true
export PATH="${HOME}/.agentjail/bin:${HOME}/.local/bin:${PATH}"
export YOLO_HOOK_AGENT=claude

echo "== check-guardrails =="
agentjail status >/dev/null || { echo "FAIL: agentjail not running" >&2; exit 1; }
echo "  agentjail daemon: ok"

agentjail try "rm -rf /" 2>&1 | grep -q deny || { echo "FAIL: agentjail try should deny rm -rf /" >&2; exit 1; }
echo "  agentjail try deny probe: ok"

payload='{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf '"$ROOT"'"}}'
result=$(echo "$payload" | bash "$ROOT/scripts/yolo/yolo-shell-guard.sh")
echo "$result" | grep -qE '"permissionDecision"[[:space:]]*:[[:space:]]*"deny"' || { echo "FAIL: yolo-shell-guard should deny (got: $result)" >&2; exit 1; }
echo "  yolo-shell-guard deny probe: ok"

if [[ -f "$ROOT/.claude/settings.local.json" ]]; then
  echo "  project .claude/settings.local.json: ok"
elif [[ -f "$ROOT/.yolo/headless" ]]; then
  echo "  project .yolo/headless marker: ok"
else
  echo "FAIL: missing headless config — nerdrack: cp .claude/settings.local.json.headless.example .claude/settings.local.json" >&2
  exit 1
fi
echo "All guardrail gates passed."
