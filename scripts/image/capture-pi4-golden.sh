#!/usr/bin/env bash
# Back-compat wrapper — use capture-golden.sh --platform pi4|pi5|auto
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/capture-golden.sh" --platform pi4 "$@"
