#!/usr/bin/env bash
# Back-compat wrapper — use build-appliance.sh --platform pi4
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/build-appliance.sh" --platform pi4 "$@"
