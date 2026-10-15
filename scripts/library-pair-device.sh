#!/usr/bin/env bash
# Laptop: give one device read/write access to one personal library repo (migration step 3).
#
#   library-pair-device.sh --repo OWNER/NAME [--device-id ID] [--dry-run] [--replace]
#   library-pair-device.sh --repo OWNER/NAME [--device-id ID] --unpair [--purge-key] [--dry-run]
#
# Device target, from the environment (never committed): MPE_PI_SSH (a host, or user@host),
# plus MPE_PI_USER / PI_USER when MPE_PI_SSH has no user; or PI_USER and PI_HOST.
# Needs `gh` logged in with admin rights on the repo. Fully non-interactive: ssh runs with
# BatchMode, nothing prompts. Every step is idempotent; a rerun changes nothing.
#
# Order (device steps run over ssh, the private key never leaves the device):
#   1. device: create ~/.ssh/mpe_library_ed25519 if missing (mode 600); read back the .pub only
#   2. device: trust github.com's ed25519 host key if missing, after checking its fingerprint
#              against GitHub's published one; a mismatch refuses, before any access is granted
#   3. laptop: add the .pub as deploy key "mpe-device:<device-id>" (write access) via `gh api`
#   4. device: add or replace only the `Host github-library` block in ~/.ssh/config
#              (timestamped backup first, every other block untouched)
#   5. device: `git ls-remote` over that alias; prints the remote URL to use
# --unpair deletes the deploy key and the config block; the private key stays unless --purge-key.
#
# Exit: 0 ok, 1 refused or failed, 2 usage.

set -euo pipefail

# GitHub's published ed25519 host key fingerprint.
GITHUB_ED25519_FP="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"

REPO=""
DEVICE_ID=""
DRY=0
REPLACE=0
UNPAIR=0
PURGE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="${2:?--repo needs OWNER/NAME}"; shift 2 ;;
        --device-id) DEVICE_ID="${2:?--device-id needs a value}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        --replace) REPLACE=1; shift ;;
        --unpair) UNPAIR=1; shift ;;
        --purge-key) PURGE=1; shift ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$REPO" ]; then echo "ERROR: --repo OWNER/NAME is required." >&2; exit 2; fi
if ! [[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    echo "ERROR: --repo must look like OWNER/NAME." >&2; exit 2
fi
if [ "$PURGE" = 1 ] && [ "$UNPAIR" = 0 ]; then
    echo "ERROR: --purge-key only applies with --unpair." >&2; exit 2
fi
if [ -n "$DEVICE_ID" ] && ! [[ "$DEVICE_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "ERROR: --device-id may contain only letters, digits, . _ -" >&2; exit 2
fi

TARGET=""
if [ -n "${MPE_PI_SSH:-}" ]; then
    TARGET="$MPE_PI_SSH"
    case "$TARGET" in
        *@*) ;;
        *)
            _u="${MPE_PI_USER:-${PI_USER:-}}"
            if [ -n "$_u" ]; then TARGET="$_u@$TARGET"; fi
            ;;
    esac
elif [ -n "${PI_HOST:-}" ] && [ -n "${PI_USER:-}" ]; then
    TARGET="$PI_USER@$PI_HOST"
fi
if [ -z "$TARGET" ]; then
    echo "ERROR: set the device target: MPE_PI_SSH, or PI_USER and PI_HOST." >&2
    exit 2
fi

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15)

# rsh SCRIPT [ARG...]: run SCRIPT on the device with the given arguments (safe charset only).
rsh() {
    local script="$1"
    shift
    # shellcheck disable=SC2029  # arguments are validated to a safe charset and expand here on purpose
    ssh "${SSH_OPTS[@]}" "$TARGET" "bash -s -- $*" <<<"$script"
}

if [ -z "$DEVICE_ID" ]; then
    DEVICE_ID="$(ssh "${SSH_OPTS[@]}" "$TARGET" hostname | tr -d '\r\n')"
    if ! [[ "$DEVICE_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "ERROR: could not derive a usable device id from the device hostname; pass --device-id." >&2
        exit 2
    fi
fi
TITLE="mpe-device:$DEVICE_ID"
REMOTE_URL="git@github-library:$REPO.git"

say() { echo "[pair] $*"; }
would() { echo "[pair] would: $*"; }

# ---------------------------------------------------------------- remote scripts
read -r -d '' R_KEY <<'EOF' || true
set -euo pipefail
id="$1"; dry="$2"
k="$HOME/.ssh/mpe_library_ed25519"
if [ ! -f "$k" ]; then
    if [ "$dry" = 1 ]; then echo "STATE would-create"; exit 0; fi
    umask 077
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -q -t ed25519 -N "" -C "mpe-library:$id" -f "$k" >/dev/null
    echo "STATE created"
else
    echo "STATE present"
    if [ "$dry" != 1 ]; then chmod 600 "$k"; fi
fi
if [ ! -f "$k.pub" ]; then
    if [ "$dry" = 1 ]; then echo "STATE would-rebuild-pub"; exit 0; fi
    ssh-keygen -y -f "$k" >"$k.pub"
fi
echo "PUB $(cut -d' ' -f1,2 "$k.pub")"
EOF

read -r -d '' R_HOSTKEY <<'EOF' || true
set -euo pipefail
dry="$1"; expected="$2"
kh="$HOME/.ssh/known_hosts"
if [ -f "$kh" ] && ssh-keygen -F github.com -f "$kh" 2>/dev/null | grep -q 'ssh-ed25519'; then
    echo "HOSTKEY present"; exit 0
fi
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
ssh-keyscan -t ed25519 github.com 2>/dev/null | grep -v '^#' >"$tmp" || true
fp="$(ssh-keygen -lf "$tmp" 2>/dev/null | awk 'NR==1{print $2}')"
if [ -z "$fp" ]; then echo "ERROR: could not fetch github.com's host key" >&2; exit 4; fi
if [ "$fp" != "$expected" ]; then
    echo "ERROR: github.com host key fingerprint $fp does not match the published $expected; not trusting it" >&2
    exit 3
fi
if [ "$dry" = 1 ]; then echo "HOSTKEY would-add"; exit 0; fi
umask 077
mkdir -p "$HOME/.ssh"
cat "$tmp" >>"$kh"
echo "HOSTKEY added"
EOF

read -r -d '' R_CONFIG <<'EOF' || true
set -euo pipefail
mode="$1"; dry="$2"   # mode: replace | remove
cfg="$HOME/.ssh/config"
work="$(mktemp)"; blk="$(mktemp)"
trap 'rm -f "$work" "$blk"' EXIT
cat >"$blk" <<'BLOCK'
Host github-library
    HostName github.com
    User git
    IdentityFile ~/.ssh/mpe_library_ed25519
    IdentitiesOnly yes
BLOCK
src="$cfg"; [ -f "$cfg" ] || src=/dev/null
awk -v mode="$mode" -v blk="$blk" '
function emit(   l) { while ((getline l < blk) > 0) print l; close(blk); done = 1 }
{
    if (skipping) {
        if ($0 ~ /^[ \t]*([Hh][Oo][Ss][Tt]|[Mm][Aa][Tt][Cc][Hh])[ \t]/) {
            skipping = 0
            if (mode == "replace") print ""
        } else next
    }
    if ($0 ~ /^[ \t]*[Hh][Oo][Ss][Tt][ \t]+github-library[ \t]*$/) {
        if (mode == "replace" && !done) emit()
        skipping = 1
        next
    }
    print
    lastblank = ($0 ~ /^[ \t]*$/)
    seen = 1
}
END {
    if (mode == "replace" && !done) {
        if (seen && !lastblank) print ""
        emit()
    }
}' "$src" >"$work"
if [ -f "$cfg" ] && cmp -s "$work" "$cfg"; then echo "CONFIG unchanged"; exit 0; fi
if [ ! -f "$cfg" ] && [ ! -s "$work" ]; then echo "CONFIG unchanged"; exit 0; fi
if [ "$dry" = 1 ]; then echo "CONFIG would-change"; exit 0; fi
umask 077
mkdir -p "$HOME/.ssh"
if [ -f "$cfg" ]; then
    bak="$cfg.bak.$(date +%Y%m%d%H%M%S)"
    cp -p "$cfg" "$bak"
    echo "BACKUP $bak"
fi
cp "$work" "$cfg"
chmod 600 "$cfg"
echo "CONFIG changed"
EOF

read -r -d '' R_VERIFY <<'EOF' || true
set -uo pipefail
url="$1"; sleep_s="$2"
for attempt in 1 2 3; do
    if GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes" git ls-remote "$url" HEAD >/dev/null; then
        echo "VERIFY ok"; exit 0
    fi
    sleep "$sleep_s"
done
echo "VERIFY failed" >&2
exit 1
EOF

read -r -d '' R_PURGE <<'EOF' || true
set -euo pipefail
rm -f "$HOME/.ssh/mpe_library_ed25519" "$HOME/.ssh/mpe_library_ed25519.pub"
echo "PURGE done"
EOF

# ---------------------------------------------------------------- GitHub keys
# Prints "id<TAB>title<TAB>key<TAB>read_only" per deploy key.
list_keys() {
    gh api "repos/$REPO/keys" --paginate \
        --jq '.[] | [.id, .title, .key, .read_only] | @tsv'
}
body_of() { awk '{print $2}' <<<"$1"; }

fail() { echo "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------- unpair
if [ "$UNPAIR" = 1 ]; then
    pub=""
    out="$(rsh "$R_KEY" "$DEVICE_ID" 1)" || fail "could not inspect the device key"
    pub="$(sed -n 's/^PUB //p' <<<"$out")"
    pubbody=""
    if [ -n "$pub" ]; then pubbody="$(body_of "$pub")"; fi
    while IFS=$'\t' read -r kid ktitle kkey _; do
        [ -n "$kid" ] || continue
        kbody="$(body_of "$kkey")"
        if [ "$ktitle" = "$TITLE" ] || { [ -n "$pubbody" ] && [ "$kbody" = "$pubbody" ]; }; then
            if [ "$DRY" = 1 ]; then would "delete deploy key $kid ($ktitle) from $REPO"
            else gh api -X DELETE "repos/$REPO/keys/$kid" >/dev/null; say "deleted deploy key $kid ($ktitle)"; fi
        fi
    done < <(list_keys)
    out="$(rsh "$R_CONFIG" remove "$DRY")" || fail "could not edit the device ssh config"
    say "device config: $(grep -E '^(CONFIG|BACKUP)' <<<"$out" | tr '\n' ' ')"
    if [ "$PURGE" = 1 ]; then
        if [ "$DRY" = 1 ]; then would "delete the device's private key and .pub"
        else rsh "$R_PURGE" >/dev/null; say "private key removed from the device"; fi
    else
        say "private key left on the device (--purge-key to remove it)"
    fi
    exit 0
fi

# ---------------------------------------------------------------- pair
# 1. device key
out="$(rsh "$R_KEY" "$DEVICE_ID" "$DRY")" || fail "could not prepare the device key"
pub="$(sed -n 's/^PUB //p' <<<"$out")"
if grep -q '^STATE would-' <<<"$out"; then would "create the device key (ed25519, mode 600)"; fi
if grep -q '^STATE created' <<<"$out"; then say "created the device key"; fi
if grep -q '^STATE present' <<<"$out"; then say "device key present"; fi

# 2. github.com host key, checked before any access is granted
out="$(rsh "$R_HOSTKEY" "$DRY" "$GITHUB_ED25519_FP")" || fail "github.com host key not trusted; nothing was granted"
say "github.com host key: $(sed -n 's/^HOSTKEY //p' <<<"$out")"

# 3. deploy key
if [ -z "$pub" ]; then
    would "add deploy key $TITLE (write access) to $REPO"
else
    pubbody="$(body_of "$pub")"
    same_id=""; same_ro=""; clash_ids=()
    while IFS=$'\t' read -r kid ktitle kkey kro; do
        [ -n "$kid" ] || continue
        if [ "$(body_of "$kkey")" = "$pubbody" ]; then same_id="$kid"; same_ro="$kro"
        elif [ "$ktitle" = "$TITLE" ]; then clash_ids+=("$kid"); fi
    done < <(list_keys)
    add=0
    if [ -n "$same_id" ] && [ "$same_ro" != "true" ]; then
        say "deploy key already on $REPO (id $same_id, write access)"
    elif [ -n "$same_id" ]; then
        [ "$REPLACE" = 1 ] || fail "deploy key $same_id on $REPO is read-only; rerun with --replace to swap it for a writable one"
        if [ "$DRY" = 1 ]; then would "delete read-only key $same_id and re-add it with write access"
        else gh api -X DELETE "repos/$REPO/keys/$same_id" >/dev/null; say "deleted read-only key $same_id"; fi
        add=1
    elif [ "${#clash_ids[@]}" -gt 0 ]; then
        [ "$REPLACE" = 1 ] || fail "$REPO already has a deploy key titled $TITLE with a different key; rerun with --replace to swap it"
        for kid in "${clash_ids[@]}"; do
            if [ "$DRY" = 1 ]; then would "delete old key $kid ($TITLE)"
            else gh api -X DELETE "repos/$REPO/keys/$kid" >/dev/null; say "deleted old key $kid ($TITLE)"; fi
        done
        add=1
    else
        add=1
    fi
    if [ "$add" = 1 ]; then
        if [ "$DRY" = 1 ]; then would "add deploy key $TITLE (write access) to $REPO"
        else
            gh api -X POST "repos/$REPO/keys" -f "title=$TITLE" -f "key=$pub" -F read_only=false >/dev/null
            say "added deploy key $TITLE (write access)"
        fi
    fi
fi

# 4. ssh config block on the device
out="$(rsh "$R_CONFIG" replace "$DRY")" || fail "could not edit the device ssh config"
say "device config: $(grep -E '^(CONFIG|BACKUP)' <<<"$out" | tr '\n' ' ')"

# 5. verify
if [ "$DRY" = 1 ]; then
    would "git ls-remote $REMOTE_URL HEAD on the device"
else
    rsh "$R_VERIFY" "$REMOTE_URL" "${MPE_PAIR_RETRY_SLEEP:-3}" >/dev/null \
        || fail "the device cannot reach $REPO over $REMOTE_URL (is the deploy key added, is the repo name right?)"
    say "verified: the device can read $REPO"
fi
echo "Remote URL to use: $REMOTE_URL"
