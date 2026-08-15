#!/usr/bin/env bash
# Hard-deny hook for headless YOLO (Cursor + Claude Code).
# Supports Cursor beforeShellExecution and Claude PreToolUse (Bash) payloads.
set -euo pipefail

input=$(cat)
command=$(
  python3 -c "
import json, sys
d = json.load(sys.stdin)
cmd = d.get('command')
if not cmd:
    ti = d.get('tool_input') or {}
    cmd = ti.get('command', '')
print(cmd)
" <<<"$input"
)

deny() {
  local msg="$1"
  if [[ "${YOLO_HOOK_AGENT:-cursor}" == "claude" ]]; then
    python3 -c "
import json, sys
print(json.dumps({
  'hookSpecificOutput': {
    'hookEventName': 'PreToolUse',
    'permissionDecision': 'deny',
    'permissionDecisionReason': sys.argv[1],
  }
}))
" "$msg"
  else
    printf '{"permission":"deny","user_message":"%s"}\n' "$msg"
  fi
  exit 0
}

allow() {
  if [[ "${YOLO_HOOK_AGENT:-cursor}" == "claude" ]]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}\n'
  else
    printf '{"permission":"allow"}\n'
  fi
  exit 0
}

# Destructive absolute-path deletes
if echo "$command" | grep -qE '(^|[;&|[:space:]])rm[[:space:]]+-[a-zA-Z]*r[a-zA-Z]*f[[:space:]]+/'; then
  deny "YOLO guardrail: rm -rf on absolute paths is blocked"
fi

# Pipe to shell
if echo "$command" | grep -qE '(curl|wget)[^|]*\|[[:space:]]*(ba)?sh'; then
  deny "YOLO guardrail: pipe-to-shell blocked"
fi

# Force push protected branches
if echo "$command" | grep -qE 'git push[^;|]*(--force|-f)[^;|]*(main|master|dev)'; then
  deny "YOLO guardrail: force-push to protected branches blocked"
fi

# Direct push to main/dev
if echo "$command" | grep -qE 'git push[^;|]*(origin[[:space:]]+)?(main|dev)([[:space:]]|$)'; then
  deny "YOLO guardrail: direct push to main/dev blocked — use yolo/* branch + PR"
fi

# MPE appliance writes (nerdrack cannot reach Pi; block even if misconfigured)
if echo "$command" | grep -qE '(^|[;&|[:space:]])(bash[[:space:]]+)?(\./)?scripts/deploy-all\.sh'; then
  deny "YOLO guardrail: deploy-all.sh is Mitch-only"
fi
if echo "$command" | grep -qE 'set-audio-profile\.sh|set-surge-audio\.sh|set-midi-sync\.sh'; then
  deny "YOLO guardrail: Pi audio/systemd profile scripts are Mitch-only"
fi
if echo "$command" | grep -qE '(^|[;&|[:space:]])mpe[[:space:]]+restart'; then
  deny "YOLO guardrail: mpe restart is blocked on nerdrack"
fi
if echo "$command" | grep -qE '(^|[;&|[:space:]])(ssh|scp|rsync)[^;|]*raspberrypi'; then
  deny "YOLO guardrail: direct Pi SSH/SCP/rsync blocked — Pi is LAN-only"
fi
if echo "$command" | grep -qE '(^|[;&|[:space:]])sudo[[:space:]]+apt'; then
  deny "YOLO guardrail: sudo apt on appliance/host is Mitch-only"
fi
if echo "$command" | grep -qE '(^|[;&|[:space:]])(sudo[[:space:]]+)?(poweroff|reboot|shutdown)([[:space:]]|$)'; then
  deny "YOLO guardrail: poweroff/reboot/shutdown blocked"
fi

# OneCLI vault/admin — Mitch/laptop only. YOLO agents use MCP via 10255 proxy, not 10254 admin API.
if echo "$command" | grep -qE '(^|[;&|[:space:]])(onecli-nerdrack|onecli-nerdrack\.sh)([[:space:]]|$)'; then
  deny "YOLO guardrail: onecli-nerdrack admin script blocked on nerdrack"
fi
if echo "$command" | grep -qE '(^|[;&|[:space:]])(onecli)([[:space:]]|$|-)'; then
  deny "YOLO guardrail: onecli CLI blocked — agents cannot modify vault, agents, or grants"
fi
if echo "$command" | grep -qE '(curl|wget)[^;|]*(127\.0\.0\.1:10254|localhost:10254|://127\.0\.0\.1:10254)'; then
  deny "YOLO guardrail: OneCLI admin API (10254) blocked for agents"
fi
if echo "$command" | grep -qE 'enqueue-yolo-task\.sh[^;|]*(approve|clear-gate)'; then
  deny "YOLO guardrail: YOLO queue approve/clear-gate is laptop/Mitch only"
fi

allow
