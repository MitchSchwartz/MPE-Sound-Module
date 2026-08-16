#!/bin/bash
# Install a GitHub classic PAT for Pi git pull (HTTPS only).
#
# From laptop (after PAT created in GitHub UI):
#   printf '%s' "$GITHUB_TOKEN" | ssh mitch@raspberrypi2.local \
#     'sudo bash -s -- --stdin' < scripts/setup-pi-github-pat.sh
#
# On Pi (from repo):
#   printf '%s' "$GITHUB_TOKEN" | sudo ./scripts/setup-pi-github-pat.sh --stdin
#
# On Pi (standalone copy in /tmp):
#   printf '%s' "$GITHUB_TOKEN" | sudo bash /tmp/setup-pi-github-pat.sh --stdin
#
# Token never belongs in argv, chat, or git. Rotate via GitHub → revoke old → re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/lib/paths.sh" ]; then
    # shellcheck source=lib/paths.sh
    source "$SCRIPT_DIR/lib/paths.sh"
elif [ -f /etc/mpe/mpe.env ]; then
    # Standalone run (e.g. /tmp/setup-pi-github-pat.sh) — load appliance paths only.
    # shellcheck disable=SC1091
    source /etc/mpe/mpe.env
fi

FROM_STDIN=false

usage() {
    cat <<'EOF'
Usage: setup-pi-github-pat.sh --stdin

  --stdin   Read classic PAT from stdin (one line, no echo).

Writes:
  /etc/mpe/git-credentials   root:mitch 640  — git credential store
  /etc/mpe/github.env        root:mitch 640  — GITHUB_TOKEN= (for future deploy hooks)

Configures git for PI_USER:
  credential.helper store --file=/etc/mpe/git-credentials
  remote.origin.url → https://github.com/MitchSchwartz/… (module + library)

Verify: GIT_TERMINAL_PROMPT=0 git -C ~/MPE-Module ls-remote origin HEAD
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --stdin) FROM_STDIN=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

PI_USER="${MPE_PI_USER:-mitch}"
TOKEN=""
CRED_FILE="/etc/mpe/git-credentials"
ENV_FILE="/etc/mpe/github.env"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: run as root (sudo)." >&2
    exit 1
fi

if [ "$FROM_STDIN" != true ]; then
    echo "ERROR: pass --stdin; do not put the token on the command line." >&2
    exit 1
fi

IFS= read -r TOKEN || true
TOKEN="${TOKEN//$'\n'/}"
TOKEN="${TOKEN//$'\r'/}"

if [ -z "$TOKEN" ]; then
    echo "ERROR: empty token on stdin." >&2
    exit 1
fi

case "$TOKEN" in
    ghp_*|github_pat_*) ;;
    *)
        echo "ERROR: token does not look like a GitHub PAT (expected ghp_… or github_pat_…)." >&2
        exit 1
        ;;
esac

mkdir -p /etc/mpe
install -m 640 -o root -g "$PI_USER" /dev/null "$CRED_FILE"
printf 'https://x-access-token:%s@github.com\n' "$TOKEN" > "$CRED_FILE"
chmod 640 "$CRED_FILE"
chown root:"$PI_USER" "$CRED_FILE"

{
    echo "# Pi GitHub classic PAT — git pull only. Rotate: revoke in GitHub, re-run setup."
    echo "# Mitch-only. Not for Racknerd (om-yolo uses separate OneCLI secret)."
    printf 'GITHUB_TOKEN=%s\n' "$TOKEN"
} > "$ENV_FILE"
chmod 640 "$ENV_FILE"
chown root:"$PI_USER" "$ENV_FILE"

_as_mitch() {
    sudo -u "$PI_USER" -H env HOME="/home/$PI_USER" "$@"
}

_git_https_remote() {
    local repo="$1"
    echo "https://github.com/MitchSchwartz/${repo}.git"
}

_configure_repo() {
    local dir="$1"
    local repo="$2"
    if [ ! -d "$dir/.git" ]; then
        echo "  skip $dir (not a git repo)"
        return 0
    fi
    local url
    url="$(_git_https_remote "$repo")"
    echo "  $dir → $url"
    _as_mitch git -C "$dir" remote set-url origin "$url"
    _as_mitch git -C "$dir" config credential.helper "store --file=$CRED_FILE"
}

echo "Configuring git credential helper for $PI_USER ..."
_as_mitch git config --global credential.helper "store --file=$CRED_FILE"

# Drop any user-owned credential files from older setups.
for old in "/home/$PI_USER/.git-credentials" "/home/$PI_USER/.config/git/credentials"; do
    if [ -f "$old" ]; then
        echo "Removing legacy credential file: $old"
        rm -f "$old"
    fi
done

if [ -f "/home/$PI_USER/.ssh/config" ] && grep -q 'Host github.com' "/home/$PI_USER/.ssh/config" 2>/dev/null; then
    echo "NOTE: ~/.ssh/config still has a github.com Host block (M-Ferda era)."
    echo "      HTTPS + PAT is authoritative now; remove the SSH block when convenient."
fi

MODULE_DIR="${MPE_MODULE_REPO:-/home/$PI_USER/MPE-Module}"
LIBRARY_DIR="${MPE_PERSONAL_REPO:-/home/$PI_USER/MPE-Library}"

echo "Setting HTTPS remotes ..."
_configure_repo "$MODULE_DIR" "MPE-Sound-Module"
_configure_repo "$LIBRARY_DIR" "MPE-Library"

echo "Verifying GitHub access ..."
if ! _as_mitch env GIT_TERMINAL_PROMPT=0 git -C "$MODULE_DIR" ls-remote origin HEAD >/dev/null 2>&1; then
    echo "ERROR: git ls-remote failed for MPE-Module — check token scope (classic: repo)." >&2
    exit 1
fi
echo "✓ MPE-Module ls-remote OK"

if [ -d "$LIBRARY_DIR/.git" ]; then
    if _as_mitch env GIT_TERMINAL_PROMPT=0 git -C "$LIBRARY_DIR" ls-remote origin HEAD >/dev/null 2>&1; then
        echo "✓ MPE-Library ls-remote OK"
    else
        echo "WARN: MPE-Library ls-remote failed (clone missing or token lacks repo access)." >&2
    fi
fi

echo ""
echo "Done. Credentials: $CRED_FILE (640 root:$PI_USER)"
echo "Next (GitHub UI, Mitch-only):"
echo "  1. Remove M-Ferda collaborator from MPE-Sound-Module (+ MPE-Library if used)"
echo "  2. Revoke M-Ferda SSH keys on the M-Ferda account (if any remain)"
echo "  3. Keep om-yolo for Racknerd — separate from Pi PAT"
