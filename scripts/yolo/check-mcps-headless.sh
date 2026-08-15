#!/usr/bin/env bash
# Gate: OneCLI GitHub + current-time MCP before headless Claude YOLO sessions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

ENV_FILE="$ONECLI_ENV_FILE"
GITHUB_SECRET="${GITHUB_MCP_SECRET:-$GITHUB_MCP_SECRET_DEFAULT}"

echo "== check-mcps-headless =="

bash "$ROOT/scripts/check-onecli-github.sh" "$GITHUB_SECRET"

if [[ -x "${UVX:-$HOME/.local/bin/uvx}" || -n "$(command -v uvx || true)" ]]; then
  echo "  uvx (current-time MCP): ok"
else
  echo "  WARN: uvx not found — current-time MCP will fail until uv/uvx is installed" >&2
fi

if command -v docker >/dev/null 2>&1; then
  echo "  docker (github MCP): ok"
else
  echo "FAIL: docker not found (required for github MCP)" >&2
  exit 1
fi

if [[ -f "$ROOT/.claude/mcp.json" ]] && command -v claude >/dev/null 2>&1; then
  echo "  claude mcp list:"
  (cd "$ROOT" && claude mcp list) || {
    echo "    note: claude mcp list failed — claude-yolo.sh passes --mcp-config explicitly" >&2
  }
fi

echo "All headless MCP gates passed."
