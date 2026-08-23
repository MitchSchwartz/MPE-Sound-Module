#!/usr/bin/env bash
# Point this repo's git hooks at .githooks/ (pre-commit runs gitleaks).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

chmod +x .githooks/pre-commit scripts/secret-scan.sh
git config core.hooksPath .githooks

echo "Git hooks enabled: core.hooksPath=.githooks"
echo "Pre-commit runs: scripts/secret-scan.sh --staged"

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "Warning: gitleaks not found on PATH — install before committing." >&2
else
    gitleaks version
fi
