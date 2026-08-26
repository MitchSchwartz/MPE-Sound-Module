#!/usr/bin/env bash
# Convenience wrapper — use bake-golden.sh --platform pi5
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bake-golden.sh" --platform pi5 "$@"
