#!/usr/bin/env bash
# Shared project identity for every script in scripts/yolo/.
#
# Source at the top of any script:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck disable=SC1091
#   source "$SCRIPT_DIR/_project.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_SLUG="${PROJECT_SLUG:-mpe-module}"
GITHUB_REPO="${GITHUB_REPO:-MitchSchwartz/MPE-Sound-Module}"

ONECLI_ENV_FILE="${ONECLI_ENV_FILE:-$HOME/.onecli/${PROJECT_SLUG}.env}"
GITHUB_MCP_SECRET_DEFAULT="${GITHUB_MCP_SECRET:-github-${PROJECT_SLUG}}"

export ROOT PROJECT_SLUG GITHUB_REPO ONECLI_ENV_FILE GITHUB_MCP_SECRET_DEFAULT

# Bench modules (track_gesture, etc.) use bare imports — conftest.py is pytest-only.
export PYTHONPATH="${ROOT}/scripts/sooperlooper${PYTHONPATH:+:${PYTHONPATH}}"
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-dummy}"
export PYGAME_HIDE_SUPPORT_PROMPT="${PYGAME_HIDE_SUPPORT_PROMPT:-1}"
