#!/usr/bin/env bash
# GitHub MCP (official docker image) via OneCLI — PAT stays in vault.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/onecli-proxy-env.sh"

SECRET_NAME="${1:-github-mpe-module}"
GATEWAY_PORT="${ONECLI_GATEWAY##*:}"
DOCKER_PROXY="http://x:${ONECLI_AOC_TOKEN}@127.0.0.1:${GATEWAY_PORT}"
CA_MOUNT="/etc/ssl/certs/onecli-ca.pem"

preflight_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 \
  --cacert "${ONECLI_CA}" \
  -H "Authorization: Bearer ${SECRET_NAME}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/user" 2>/dev/null || echo "000")

case "$preflight_code" in
  200) ;;
  401)
    echo "mcp-github-via-onecli: secret '${SECRET_NAME}' rejected by GitHub (401)" >&2
    exit 1
    ;;
  000)
    echo "mcp-github-via-onecli: cannot reach api.github.com via OneCLI" >&2
    exit 1
    ;;
  *)
    echo "mcp-github-via-onecli: preflight HTTP ${preflight_code}" >&2
    exit 1
    ;;
esac

exec docker run -i --rm \
  --network host \
  -e "GITHUB_PERSONAL_ACCESS_TOKEN=${SECRET_NAME}" \
  -e "HTTP_PROXY=${DOCKER_PROXY}" \
  -e "HTTPS_PROXY=${DOCKER_PROXY}" \
  -e "http_proxy=${DOCKER_PROXY}" \
  -e "https_proxy=${DOCKER_PROXY}" \
  -e "SSL_CERT_FILE=${CA_MOUNT}" \
  -e "NODE_EXTRA_CA_CERTS=${CA_MOUNT}" \
  -v "${ONECLI_CA}:${CA_MOUNT}:ro" \
  ghcr.io/github/github-mcp-server
