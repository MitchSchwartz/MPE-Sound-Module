#!/usr/bin/env bash
# Repo lint gates wired into `mpe test all` (mirrors former scattered unittest greps).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "--- scripts/lint-systemd-units.sh ---"
bash "${ROOT}/scripts/lint-systemd-units.sh"

echo "--- scripts/lint-jack-only-paths.sh ---"
bash "${ROOT}/scripts/lint-jack-only-paths.sh"

echo "All repo lint checks passed"
