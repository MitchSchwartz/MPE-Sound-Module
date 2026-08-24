#!/usr/bin/env bash
# Back-compat wrapper — use bake-golden.sh --platform pi4|pi5
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bake-golden.sh" --platform pi4 "$@"
