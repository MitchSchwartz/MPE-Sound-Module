#!/usr/bin/env bash
# Convenience wrapper — use capture-golden.sh --platform pi5|auto
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/capture-golden.sh" --platform pi5 "$@"
