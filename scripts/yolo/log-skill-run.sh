#!/usr/bin/env bash
# Append a skill-run row to .claude/primitives/skill-log.md (audit trail).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

LOG="$ROOT/.claude/primitives/skill-log.md"
SKILLS="${YOLO_SKILLS:-}"
TASK="${YOLO_TASK:-}"
OUTCOME="${YOLO_OUTCOME:-}"
SESSION="${YOLO_SESSION:-claude-yolo}"
EXIT_CODE="${1:-0}"

[[ -f "$LOG" ]] || exit 0
[[ -n "$SKILLS" || -n "$TASK" ]] || exit 0

if [[ -z "$OUTCOME" ]]; then
  if [[ "$EXIT_CODE" -eq 0 ]]; then
    OUTCOME="ok"
  else
    OUTCOME="failed (exit ${EXIT_CODE})"
  fi
fi

WHEN="$(TZ=America/Toronto date '+%Y-%m-%d %H:%M')"
ROW="| ${WHEN} | ${SESSION} | ${SKILLS:-—} | ${TASK:-—} | ${OUTCOME} |"

{
  head -n 5 "$LOG"
  echo "$ROW"
  tail -n +6 "$LOG" 2>/dev/null || true
} > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
