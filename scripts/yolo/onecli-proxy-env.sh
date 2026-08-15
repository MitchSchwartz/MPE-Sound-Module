#!/usr/bin/env bash
# OneCLI MITM proxy env for MPE-Module headless MCP wrappers (nerdrack).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

ENV_FILE="$ONECLI_ENV_FILE"
GATEWAY="${ONECLI_GATEWAY:-http://127.0.0.1:10255}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

AOC_TOKEN="${ONECLI_AOC_TOKEN:-}"
if [[ -z "$AOC_TOKEN" ]]; then
  echo "onecli-proxy-env: ONECLI_AOC_TOKEN not set (see $ENV_FILE)" >&2
  exit 1
fi

if [[ "$AOC_TOKEN" == oc_* ]]; then
  echo "onecli-proxy-env: need aoc_* agent token, not oc_* management token" >&2
  exit 1
fi

export ONECLI_GATEWAY="$GATEWAY"
export ONECLI_AOC_TOKEN="$AOC_TOKEN"

PROXY_URL="http://x:${AOC_TOKEN}@${GATEWAY#http://}"
export HTTP_PROXY="$PROXY_URL"
export HTTPS_PROXY="$PROXY_URL"
export http_proxy="$PROXY_URL"
export https_proxy="$PROXY_URL"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,registry.npmjs.org,registry.npmmirror.com}"
export NODE_USE_ENV_PROXY=1

ONECLI_CA="${ONECLI_CA:-$HOME/.onecli/gateway-ca.pem}"
if [[ -f "$ONECLI_CA" ]]; then
  export NODE_EXTRA_CA_CERTS="$ONECLI_CA"
  export SSL_CERT_FILE="$ONECLI_CA"
  export REQUESTS_CA_BUNDLE="$ONECLI_CA"
  export ONECLI_CA
else
  echo "onecli-proxy-env: CA cert not found at $ONECLI_CA" >&2
  exit 1
fi
