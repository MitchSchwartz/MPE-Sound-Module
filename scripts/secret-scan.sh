#!/usr/bin/env bash
# Run gitleaks against this repo. Used locally and by .githooks/pre-commit.
#
#   scripts/secret-scan.sh            # full history
#   scripts/secret-scan.sh --staged   # staged changes only (pre-commit path)
#
# THIS REPOSITORY IS PUBLIC. A hit is a blocker, not a warning.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "gitleaks not found. Install one of:" >&2
    echo "  curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.24.2/gitleaks_8.24.2_linux_x64.tar.gz | tar xz -C ~/.local/bin gitleaks" >&2
    echo "  brew install gitleaks" >&2
    exit 1
fi

CONFIG="${GITLEAKS_CONFIG:-$ROOT/.gitleaks.toml}"

if [ "${1:-}" = "--staged" ]; then
    shift
    exec gitleaks protect --staged --source "$ROOT" --config "$CONFIG" --verbose "$@"
fi

echo "gitleaks detect --source $ROOT --config $CONFIG"
exec gitleaks detect --source "$ROOT" --config "$CONFIG" --verbose "$@"
