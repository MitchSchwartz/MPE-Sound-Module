#!/usr/bin/env bash
# Headless Claude Code YOLO wrapper for MPE-Module on nerdrack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

ENV_FILE="$ONECLI_ENV_FILE"
CLAUDE="${CLAUDE_BIN:-$HOME/.local/bin/claude}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  [[ -n "${NTFY_TOPIC:-}" ]] && export NTFY_TOPIC
fi

export PATH="${HOME}/.local/bin:${HOME}/.agentjail/bin:${PATH}"
export YOLO_HOOK_AGENT=claude

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

NOTIFY="$ROOT/scripts/yolo/notify.sh"
LOG_SKILL="$ROOT/scripts/yolo/log-skill-run.sh"
TASK_LABEL="${*:-<no prompt captured>}"
SESSION_START=$(date +%s)

on_exit() {
  local code=$?
  local dur=$(( $(date +%s) - SESSION_START ))
  bash "$LOG_SKILL" "$code" 2>/dev/null || true
  if [[ $code -eq 0 ]]; then
    bash "$NOTIFY" "MPE YOLO done" "Session finished ok in ${dur}s. Task: ${TASK_LABEL:0:200}" "default" "white_check_mark"
  else
    bash "$NOTIFY" "MPE YOLO FAILED" "Exit code ${code} after ${dur}s. Task: ${TASK_LABEL:0:200}" "high" "x"
  fi
}
trap on_exit EXIT

bash "$ROOT/scripts/yolo/check-yolo-gates.sh"
bash "$ROOT/scripts/yolo/check-guardrails.sh"
bash "$ROOT/scripts/yolo/check-mcps-headless.sh"
bash "$ROOT/scripts/yolo/check-backpressure.sh"

MODEL="${YOLO_MODEL:-sonnet}"
MCP_CONFIG="${YOLO_MCP_CONFIG:-$ROOT/.claude/mcp.json}"

if [[ ! -x "$CLAUDE" ]]; then
  echo "FAIL: Claude Code not found at $CLAUDE" >&2
  exit 1
fi

if [[ ! -f "$MCP_CONFIG" ]]; then
  echo "FAIL: missing $MCP_CONFIG — run bootstrap-nerdrack.sh or copy .claude/mcp.json.headless.example" >&2
  exit 1
fi

cd "$ROOT"
"$CLAUDE" --dangerously-skip-permissions \
  --model "$MODEL" \
  --setting-sources project,local \
  --mcp-config "$MCP_CONFIG" \
  "$@"
