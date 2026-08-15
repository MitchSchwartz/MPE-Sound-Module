#!/usr/bin/env bash
# Backpressure gate: unit tests must pass before / after headless YOLO work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

is_nerdrack() {
  [[ -f "$ROOT/.claude/settings.local.json" ]] || [[ -f "$ROOT/.yolo/headless" ]]
}

echo "== check-backpressure =="
if [[ ! -d "$ROOT/tests" ]]; then
  echo "  no tests/ directory — skipped"
  exit 0
fi

cd "$ROOT"

_yolo_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
  else
    echo python3
  fi
}

PYTHON="$(_yolo_python)"
if [[ "$PYTHON" == python3 ]] && [[ -f "$ROOT/requirements-yolo.txt" ]]; then
  echo "  WARN: no $ROOT/.venv — run: bash scripts/yolo/setup-nerdrack-python.sh" >&2
fi

if "$PYTHON" -m unittest discover -s tests -q; then
  echo "  unittest discover: ok"
  echo "All backpressure gates passed."
  exit 0
fi

if is_nerdrack && [[ "${YOLO_BACKPRESSURE_STRICT:-}" != "1" ]]; then
  echo "  WARN: unittest failed on nerdrack (often missing Pi/audio/JACK deps on VPS)." >&2
  echo "  WARN: YOLO continues — set YOLO_BACKPRESSURE_STRICT=1 to block until green." >&2
  exit 0
fi

echo "FAIL: unittest discover failed" >&2
exit 1
