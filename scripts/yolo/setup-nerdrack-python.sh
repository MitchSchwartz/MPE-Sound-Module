#!/usr/bin/env bash
# Nerdrack: Python venv for MPE-Module YOLO backpressure (unittest discover).
# Run on nerdrack as claude-sandbox from repo root (after git pull).
#
# Laptop one-shot (apt + venv):
#   racknerd run MPE-Module -- bash scripts/yolo/setup-nerdrack-python.sh
# Root apt only (if pygame build fails):
#   racknerd ssh --root -- 'bash -s' < scripts/yolo/setup-nerdrack-python.sh --apt-only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

VENV="${MPE_YOLO_VENV:-$ROOT/.venv}"
REQ="${ROOT}/requirements-yolo.txt"
MARKER="${VENV}/.mpe-yolo-python-ok"

apt_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq \
    python3 \
    python3-venv \
    python3-dev \
    python3-pip \
    libsdl2-2.0-0 \
    libsdl2-dev \
    libportmidi-dev \
    libasound2-dev
}

run_apt() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "FAIL: --apt-only requires root (racknerd ssh --root)" >&2
    exit 1
  fi
  echo "== apt packages for pygame (headless nerdrack) =="
  apt_packages
  echo "  apt: ok"
}

run_venv() {
  if [[ ! -f "$REQ" ]]; then
    echo "FAIL: missing $REQ" >&2
    exit 1
  fi

  echo "== nerdrack python venv =="
  echo "  repo: $ROOT"
  echo "  venv: $VENV"

  if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "FAIL: python3-venv not installed — run as root: $0 --apt-only" >&2
    exit 1
  fi

  python3 -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  pip install -q -U pip wheel
  pip install -q -r "$REQ"

  export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-dummy}"
  export PYGAME_HIDE_SUPPORT_PROMPT=1

  echo "  running backpressure smoke..."
  cd "$ROOT"
  python -m unittest discover -s tests -q

  date -Iseconds >"$MARKER"
  echo ""
  echo "OK: venv ready — YOLO uses $VENV via check-backpressure / claude-yolo.sh"
}

case "${1:-}" in
  --apt-only) run_apt ;;
  --venv-only) run_venv ;;
  *)
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
      run_apt
      echo ""
      echo "Now as claude-sandbox:"
      echo "  cd $ROOT && bash scripts/yolo/setup-nerdrack-python.sh --venv-only"
      exit 0
    fi
    run_venv
    ;;
esac
