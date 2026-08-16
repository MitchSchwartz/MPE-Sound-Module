#!/usr/bin/env bash
# current-time MCP via uvx — pins mcp<2 (mcp-server-time breaks on mcp SDK 2.x).
set -euo pipefail

UVX="${UVX:-$HOME/.local/bin/uvx}"
if [[ ! -x "$UVX" ]]; then
  UVX="$(command -v uvx || true)"
fi
if [[ -z "$UVX" || ! -x "$UVX" ]]; then
  echo "mcp-current-time: uvx not found" >&2
  exit 1
fi

TZ="${MCP_TIMEZONE:-America/Toronto}"
exec "$UVX" --with "mcp<2" mcp-server-time --local-timezone "$TZ" "$@"
