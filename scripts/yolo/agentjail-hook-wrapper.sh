#!/usr/bin/env bash
# Portable wrapper for the agentjail hook (Cursor + Claude Code).
set -euo pipefail

HOOK_BIN="${AGENTJAIL_HOOK_BIN:-$HOME/.agentjail/bin/agentjail-hook}"
AGENT="${AGENTJAIL_AGENT:-cursor}"
if [[ "${YOLO_HOOK_AGENT:-}" == "claude" ]]; then
  AGENT="claude"
fi

if [[ -x "$HOOK_BIN" ]]; then
  exec "$HOOK_BIN" --agent="$AGENT"
fi

cat >/dev/null
if [[ "${YOLO_HOOK_AGENT:-cursor}" == "claude" ]]; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}\n'
else
  printf '{"permission":"allow"}\n'
fi
