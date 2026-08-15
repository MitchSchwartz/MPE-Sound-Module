#!/usr/bin/env bash
# Send a status ping to ntfy.sh. Source ONECLI_ENV_FILE for NTFY_TOPIC.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

TITLE="${1:-mpe-module}"
MESSAGE="${2:-}"
PRIORITY="${3:-default}"
TAGS="${4:-}"

if [[ -z "${NTFY_TOPIC:-}" && -f "$ONECLI_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ONECLI_ENV_FILE"
fi

if [[ -z "${NTFY_TOPIC:-}" ]]; then
  exit 0
fi

curl -s --max-time 10 \
  -H "Title: ${TITLE}" \
  -H "Priority: ${PRIORITY}" \
  ${TAGS:+-H "Tags: ${TAGS}"} \
  -d "${MESSAGE}" \
  "https://ntfy.sh/${NTFY_TOPIC}" >/dev/null || true
