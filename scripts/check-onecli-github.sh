#!/usr/bin/env bash
# Health check: nerdrack OneCLI gateway + github-mpe-module secret.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/yolo/_project.sh"

ENV_FILE="$ONECLI_ENV_FILE"
CA_FILE="${ONECLI_CA:-$HOME/.onecli/gateway-ca.pem}"
GATEWAY="${ONECLI_GATEWAY:-http://127.0.0.1:10255}"
SECRET="${1:-$GITHUB_MCP_SECRET_DEFAULT}"
REPO="${GITHUB_REPO:-MitchSchwartz/MPE-Sound-Module}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "check-onecli-github: missing $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

if [[ -z "${ONECLI_AOC_TOKEN:-}" ]]; then
  echo "check-onecli-github: ONECLI_AOC_TOKEN not set in $ENV_FILE" >&2
  exit 1
fi

if [[ ! -f "$CA_FILE" ]]; then
  echo "check-onecli-github: missing CA at $CA_FILE" >&2
  exit 1
fi

proxy="http://x:${ONECLI_AOC_TOKEN}@${GATEWAY#http://}"

echo "OneCLI gateway: ${GATEWAY}"
echo "Secret name: ${SECRET}"
echo "Repo: ${REPO}"

user_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 \
  --proxy "$proxy" --cacert "$CA_FILE" \
  -H "Authorization: Bearer ${SECRET}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/user" 2>/dev/null || echo "000")

repo_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 \
  --proxy "$proxy" --cacert "$CA_FILE" \
  -H "Authorization: Bearer ${SECRET}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${REPO}" 2>/dev/null || echo "000")

echo "  ${SECRET} → api.github.com/user: HTTP ${user_code}"
echo "  ${SECRET} → repos/${REPO}: HTTP ${repo_code}"

case "$user_code" in
  200) ;;
  401) echo "    FAIL — secret not accepted. Check host/header/format in OneCLI dashboard." >&2; exit 1 ;;
  407) echo "    FAIL — stale or wrong ONECLI_AOC_TOKEN in $ENV_FILE" >&2; exit 1 ;;
  000) echo "    FAIL — gateway unreachable. Is OneCLI running?" >&2; exit 1 ;;
  *) echo "    WARN — unexpected HTTP ${user_code} on /user" >&2 ;;
esac

case "$repo_code" in
  200) echo "    OK — GitHub PAT wired for ${REPO}." ;;
  404) echo "    FAIL — token cannot see ${REPO} (wrong org approval or repo scope?)" >&2; exit 1 ;;
  401) echo "    FAIL — repo request unauthorized" >&2; exit 1 ;;
  *) echo "    WARN — repo check HTTP ${repo_code}" >&2; exit 1 ;;
esac
