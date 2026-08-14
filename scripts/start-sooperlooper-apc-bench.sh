#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONUNBUFFERED=1 /usr/bin/python3 "${SCRIPT_DIR}/sooperlooper-apc-bench.py"
