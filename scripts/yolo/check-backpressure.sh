#!/usr/bin/env bash
# Backpressure gate: unit tests must pass before / after headless YOLO work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

echo "== check-backpressure =="
if [[ ! -d "$ROOT/tests" ]]; then
  echo "  no tests/ directory — skipped"
  exit 0
fi

cd "$ROOT"
python3 -m unittest discover -s tests -q
echo "  unittest discover: ok"
echo "All backpressure gates passed."
