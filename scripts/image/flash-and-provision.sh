#!/usr/bin/env bash
# Wait for SSH on a freshly flashed Pi, run first-boot, optionally apply external state.
#
#   ./scripts/image/flash-and-provision.sh \\
#     --host newpi.local --user mitch \\
#     [--state state/raspberrypi2-2026-08-23] \\
#     [--hostname mpe-bench] \\
#     [--skip-state]
#
# Requires: config/mpe.env with PI_HOST / PI_USER / SSH_KEY (or pass --host/--user).
# See docs/PI4-GOLDEN-IMAGE.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

HOST=""
USER=""
STATE=""
HOSTNAME=""
SKIP_STATE=false
WAIT_S=300

usage() {
    echo "Usage: $0 --host HOST --user USER [--state DIR] [--hostname NAME] [--skip-state] [--wait SECONDS]" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="${2:-}"; shift 2 ;;
        --user) USER="${2:-}"; shift 2 ;;
        --state) STATE="${2:-}"; shift 2 ;;
        --hostname) HOSTNAME="${2:-}"; shift 2 ;;
        --skip-state) SKIP_STATE=true; shift ;;
        --wait) WAIT_S="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

# shellcheck source=../lib/paths.sh
source "$SCRIPT_DIR/../lib/paths.sh"

[ -n "$HOST" ] && PI_HOST="$HOST"
[ -n "$USER" ] && PI_USER="$USER"

if [ -z "${PI_USER:-}" ] || [ -z "${PI_HOST:-}" ]; then
    echo "ERROR: set --host and --user, or PI_HOST/PI_USER in config/mpe.env" >&2
    exit 1
fi

_ssh() {
    ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
        -i "$SSH_KEY" "$PI_USER@$PI_HOST" "$@"
}

echo "Waiting for $PI_USER@$PI_HOST (up to ${WAIT_S}s) ..."
deadline=$((SECONDS + WAIT_S))
while [ "$SECONDS" -lt "$deadline" ]; do
    if _ssh "echo ok" >/dev/null 2>&1; then
        echo "SSH up."
        break
    fi
    sleep 5
done

if ! _ssh "echo ok" >/dev/null 2>&1; then
    echo "ERROR: SSH not reachable at $PI_HOST" >&2
    exit 1
fi

repo_path="$(mpe_pi_repo_path)"
extra_env=""
[ -n "$HOSTNAME" ] && extra_env="MPE_HOSTNAME=$HOSTNAME"

echo ""
echo "=== first-boot ==="
_ssh "cd '$repo_path' && sudo env $extra_env ./scripts/provision/first-boot.sh"

if [ "$SKIP_STATE" = false ] && [ -n "$STATE" ]; then
    echo ""
    echo "=== apply external state ==="
    PI_HOST="$PI_HOST" PI_USER="$PI_USER" SSH_KEY="$SSH_KEY" \
        "$REPO_ROOT/scripts/provision/apply-external-state.sh" --state "$STATE"
fi

echo ""
echo "=== smoke check ==="
if command -v mpe >/dev/null 2>&1; then
    MPE_CLI_CONFIG="${MPE_CLI_CONFIG:-}" PI_HOST="$PI_HOST" PI_USER="$PI_USER" \
        mpe ping 2>/dev/null || true
    mpe status 2>/dev/null || true
else
    _ssh "systemctl is-active mpe-jackd surge-xt-cli touch-patch-browser 2>/dev/null || true"
fi

echo ""
echo "flash-and-provision: done"
echo "  Play the instrument — automated checks do not cover the thing that matters."
echo "  Fill the rehearsal row in docs/RESTORE.md when satisfied."
