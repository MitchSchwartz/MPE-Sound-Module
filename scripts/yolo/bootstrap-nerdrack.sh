#!/usr/bin/env bash
# One-time nerdrack provisioning for MPE-Module Claude YOLO lane.
# Run on nerdrack as claude-sandbox from repo root after clone + git pull.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

NERDRACK_USER="${NERDRACK_USER:-claude-sandbox}"
REPO_PATH="${NERDRACK_REPO:-$HOME/workspace/MPE-Module}"

echo "== bootstrap-nerdrack (MPE-Module / Claude Code) =="
echo "  repo: $ROOT"
echo "  user: $(whoami)"

chmod +x "$ROOT"/scripts/yolo/*.sh "$ROOT"/scripts/check-onecli-github.sh 2>/dev/null || true

mkdir -p "$ROOT/.claude"

if [[ ! -f "$ROOT/.claude/mcp.json" ]]; then
  sed "s|/home/claude-sandbox/workspace/MPE-Module|$ROOT|g" \
    "$ROOT/.claude/mcp.json.headless.example" > "$ROOT/.claude/mcp.json"
  echo "  wrote .claude/mcp.json"
else
  echo "  .claude/mcp.json already exists — skipped"
fi

if [[ ! -f "$ROOT/.claude/settings.local.json" ]]; then
  cp "$ROOT/.claude/settings.local.json.headless.example" "$ROOT/.claude/settings.local.json"
  echo "  wrote .claude/settings.local.json"
else
  echo "  .claude/settings.local.json already exists — skipped"
fi

if [[ ! -f "$HOME/.onecli/mpe-module.env" ]]; then
  cat <<EOF

WARN: missing ~/.onecli/mpe-module.env
Create on nerdrack (mode 600):

  ONECLI_AOC_TOKEN=<aoc_* from OneCLI dashboard — IO MPE Module agent>
  GITHUB_MCP_SECRET=github-mpe-module
  YOLO_REPO=${GITHUB_REPO:-MitchSchwartz/MPE-Sound-Module}
  NTFY_TOPIC=<secret topic string>

OneCLI dashboard: add secret github-mpe-module (GitHub PAT, Bearer {value}) scoped to ${GITHUB_REPO:-MitchSchwartz/MPE-Sound-Module}.

EOF
else
  echo "  ~/.onecli/mpe-module.env: ok"
fi

echo ""
echo "Running gate checks..."
bash "$ROOT/scripts/yolo/check-guardrails.sh"
bash "$ROOT/scripts/yolo/check-mcps-headless.sh"
bash "$ROOT/scripts/yolo/check-backpressure.sh"

echo ""
echo "Bootstrap complete."
echo "Smoke (after queue approve on laptop):"
echo "  YOLO_TASK_ID=<id> bash scripts/yolo/claude-yolo.sh -p \"say hello\""
