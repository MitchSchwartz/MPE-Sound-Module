#!/usr/bin/env bash
# scripts/library-pair-device.sh against a fake ssh and a fake gh.
#
# The fake ssh runs the remote command locally with HOME pointed at a scratch "device"
# home, so the real ssh-keygen, awk and the real remote scripts execute; only the network
# (ssh transport, gh, ssh-keyscan, git ls-remote) is faked. Nothing here touches a real
# device or GitHub.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/library-pair-device.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

BIN="$TMP/bin"
mkdir -p "$BIN"

# GitHub's published ed25519 host key (public; its fingerprint is pinned in the script).
GH_HOSTKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"

cat >"$BIN/ssh" <<'EOF'
#!/usr/bin/env bash
echo "ssh $*" >>"$FAKE_LOG_DIR/ssh.log"
while [ $# -gt 0 ]; do
    case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac
done
shift   # the target
HOME="$FAKE_HOME" exec bash -c "$*"
EOF
cat >"$BIN/hostname" <<'EOF'
#!/usr/bin/env bash
echo "fake-host-1"
EOF
cat >"$BIN/ssh-keyscan" <<'EOF'
#!/usr/bin/env bash
echo "# github.com:22 SSH-2.0-fake" >&2
cat "$FAKE_HOSTKEY_FILE"
EOF
cat >"$BIN/git" <<'EOF'
#!/usr/bin/env bash
echo "git $*" >>"$FAKE_LOG_DIR/git.log"
[ "$1" = ls-remote ] || exit 99
[ -z "${FAKE_LSREMOTE_FAIL:-}" ]
EOF
cat >"$BIN/gh" <<'EOF'
#!/usr/bin/env bash
echo "gh $*" >>"$FAKE_LOG_DIR/gh.log"
db="$FAKE_LOG_DIR/gh.db"
touch "$db"
shift   # api
method=GET; title=""; key=""; ro=""; path=""
while [ $# -gt 0 ]; do
    case "$1" in
        -X) method="$2"; shift 2 ;;
        -f) case "$2" in title=*) title="${2#title=}" ;; key=*) key="${2#key=}" ;; esac; shift 2 ;;
        -F) ro="${2#read_only=}"; shift 2 ;;
        --paginate) shift ;;
        --jq) shift 2 ;;
        *) path="$1"; shift ;;
    esac
done
case "$method" in
    GET) cat "$db" ;;
    POST) n=$(( $(wc -l <"$db") + 100 )); printf '%s\t%s\t%s\t%s\n' "$n" "$title" "$key" "$ro" >>"$db" ;;
    DELETE) id="${path##*/}"; grep -v "^$id	" "$db" >"$db.new" || true; mv "$db.new" "$db" ;;
esac
EOF
chmod +x "$BIN"/*

new_case() {
    CASE="$TMP/case-$1"
    rm -rf "$CASE"
    mkdir -p "$CASE/home/.ssh" "$CASE/logs"
    export FAKE_HOME="$CASE/home" FAKE_LOG_DIR="$CASE/logs" FAKE_HOSTKEY_FILE="$CASE/hostkey"
    echo "github.com $GH_HOSTKEY" >"$FAKE_HOSTKEY_FILE"
    unset FAKE_LSREMOTE_FAIL
}

pair() {
    OUT="$(PATH="$BIN:$PATH" MPE_PI_SSH=device.invalid MPE_PI_USER=someuser MPE_PAIR_RETRY_SLEEP=0 \
        "$SCRIPT" --repo owner/lib --device-id dev1 "$@" 2>&1)" && RC=0 || RC=$?
}

has() { grep -qF -- "$1" <<<"$OUT" || fail "expected output to contain: $1
$OUT"; }
snap() { (cd "$FAKE_HOME" && find . -type f -exec sha1sum {} + | sort) ; }

# --- 1. first run
new_case first
KEY="$FAKE_HOME/.ssh/mpe_library_ed25519"
pair
[ "$RC" = 0 ] || fail "first run rc=$RC: $OUT"
[ -f "$KEY" ] || fail "key not created"
[ -f "$KEY.pub" ] || fail "pub not created"
[ "$(stat -c %a "$KEY")" = 600 ] || fail "private key mode"
grep -q 'mpe-library:dev1' "$KEY.pub" || fail "key comment"
[ "$(wc -l <"$FAKE_LOG_DIR/gh.db")" = 1 ] || fail "expected exactly one deploy key"
grep -q "^100	mpe-device:dev1	$(cut -d' ' -f1,2 "$KEY.pub")	false$" "$FAKE_LOG_DIR/gh.db" || fail "deploy key row wrong"
grep -q 'Host github-library' "$FAKE_HOME/.ssh/config" || fail "config block missing"
grep -q 'IdentitiesOnly yes' "$FAKE_HOME/.ssh/config" || fail "IdentitiesOnly missing"
grep -q '^github.com ssh-ed25519' "$FAKE_HOME/.ssh/known_hosts" || fail "host key not added"
grep -q 'ls-remote git@github-library:owner/lib.git HEAD' "$FAKE_LOG_DIR/git.log" || fail "verify not run"
has "Remote URL to use: git@github-library:owner/lib.git"
if grep -rq 'PRIVATE KEY' "$FAKE_LOG_DIR"; then fail "private key reached a log"; fi
ok "first run"

# --- 2. rerun is a no-op
before="$(snap)"; db_before="$(cat "$FAKE_LOG_DIR/gh.db")"
: >"$FAKE_LOG_DIR/gh.log"
pair
[ "$RC" = 0 ] || fail "rerun rc=$RC: $OUT"
[ "$(snap)" = "$before" ] || fail "rerun changed the device files"
[ "$(cat "$FAKE_LOG_DIR/gh.db")" = "$db_before" ] || fail "rerun changed the deploy keys"
if grep -q 'POST\|DELETE' "$FAKE_LOG_DIR/gh.log"; then fail "rerun wrote to GitHub"; fi
[ "$(find "$FAKE_HOME/.ssh" -name 'config.bak.*' | wc -l)" = 0 ] || fail "rerun made a backup"
has "already on owner/lib"
ok "rerun no-op"

# --- 3. title clash
new_case clash
printf '5\tmpe-device:dev1\tssh-ed25519 AAAAOTHERBODY\tfalse\n' >"$FAKE_LOG_DIR/gh.db"
pair
[ "$RC" = 1 ] || fail "clash should fail, rc=$RC"
has "--replace"
[ "$(cat "$FAKE_LOG_DIR/gh.db")" = "$(printf '5\tmpe-device:dev1\tssh-ed25519 AAAAOTHERBODY\tfalse')" ] || fail "clash changed keys"
ok "title clash refused"

# --- 4. --replace
pair --replace
[ "$RC" = 0 ] || fail "replace rc=$RC: $OUT"
[ "$(wc -l <"$FAKE_LOG_DIR/gh.db")" = 1 ] || fail "replace should leave one key"
if grep -q AAAAOTHERBODY "$FAKE_LOG_DIR/gh.db"; then fail "old key survived --replace"; fi
grep -q "mpe-device:dev1" "$FAKE_LOG_DIR/gh.db" || fail "new key missing"
ok "--replace"

# --- 5. unpair
KEY="$FAKE_HOME/.ssh/mpe_library_ed25519"
printf 'Host other\n    HostName example.invalid\n' >"$FAKE_HOME/.ssh/config.other"
pair --unpair
[ "$RC" = 0 ] || fail "unpair rc=$RC: $OUT"
[ ! -s "$FAKE_LOG_DIR/gh.db" ] || fail "deploy key survived unpair"
if grep -q 'github-library' "$FAKE_HOME/.ssh/config"; then fail "config block survived unpair"; fi
[ -f "$KEY" ] || fail "unpair removed the private key without --purge-key"
pair --unpair --purge-key
[ "$RC" = 0 ] || fail "purge rc=$RC"
[ ! -e "$KEY" ] || fail "--purge-key left the private key"
[ ! -e "$KEY.pub" ] || fail "--purge-key left the pub"
ok "unpair, key kept, then purged"

# --- 6. fingerprint mismatch is refused, and nothing is granted
new_case fp
ssh-keygen -q -t ed25519 -N "" -f "$CASE/evil" -C x
echo "github.com $(cut -d' ' -f1,2 "$CASE/evil.pub")" >"$FAKE_HOSTKEY_FILE"
pair
[ "$RC" = 1 ] || fail "mismatch should fail, rc=$RC"
has "does not match"
[ ! -e "$FAKE_HOME/.ssh/known_hosts" ] || fail "mismatched key was trusted"
[ ! -s "$FAKE_LOG_DIR/gh.db" ] || fail "key granted despite mismatch"
[ ! -e "$FAKE_LOG_DIR/git.log" ] || fail "verify ran despite mismatch"
ok "fingerprint mismatch refused"

# --- 7. only the github-library block is replaced; other blocks byte-identical
new_case cfg
cat >"$FAKE_HOME/.ssh/config" <<'EOF'
# keep me
Host alpha
    HostName alpha.invalid
    User a

Host github-library
    HostName old.invalid
    User wrong
    IdentityFile ~/.ssh/old

Host beta
    HostName beta.invalid
    Port 2222
EOF
pair
[ "$RC" = 0 ] || fail "cfg rc=$RC: $OUT"
cfg="$FAKE_HOME/.ssh/config"
expected="$(cat <<'EOF'
# keep me
Host alpha
    HostName alpha.invalid
    User a

Host github-library
    HostName github.com
    User git
    IdentityFile ~/.ssh/mpe_library_ed25519
    IdentitiesOnly yes

Host beta
    HostName beta.invalid
    Port 2222
EOF
)"
[ "$(cat "$cfg")" = "$expected" ] || fail "config wrong:
$(cat "$cfg")"
bak="$(find "$FAKE_HOME/.ssh" -name 'config.bak.*' | head -1)"
grep -q 'old.invalid' "$bak" || fail "backup does not hold the original"
[ "$(stat -c %a "$cfg")" = 600 ] || fail "config mode"
pair
[ "$(cat "$cfg")" = "$expected" ] || fail "rerun changed config"
[ "$(find "$FAKE_HOME/.ssh" -name 'config.bak.*' | wc -l)" = 1 ] || fail "rerun made another backup"
ok "config block replaced, others untouched, backup made"

# --- 8. dry run writes nothing
new_case dry
pair --dry-run
[ "$RC" = 0 ] || fail "dry rc=$RC: $OUT"
[ -z "$(ls -A "$FAKE_HOME/.ssh")" ] || fail "dry run wrote on the device"
if grep -q 'POST\|DELETE' "$FAKE_LOG_DIR/gh.log" 2>/dev/null; then fail "dry run wrote to GitHub"; fi
has "would:"
ok "dry run"

# --- 9. device target comes from the environment; none is a usage error
new_case env
OUT="$(PATH="$BIN:$PATH" env -u MPE_PI_SSH -u PI_HOST -u PI_USER "$SCRIPT" --repo owner/lib 2>&1)" && RC=0 || RC=$?
[ "$RC" = 2 ] || fail "missing target should be a usage error, rc=$RC"
OUT="$(PATH="$BIN:$PATH" PI_USER=u PI_HOST=h.invalid MPE_PAIR_RETRY_SLEEP=0 "$SCRIPT" --repo owner/lib 2>&1)" && RC=0 || RC=$?
[ "$RC" = 0 ] || fail "PI_USER/PI_HOST path rc=$RC: $OUT"
grep -q 'u@h.invalid' "$FAKE_LOG_DIR/ssh.log" || fail "target not built from PI_USER@PI_HOST"
grep -q 'ssh.* hostname' "$FAKE_LOG_DIR/ssh.log" || fail "default device id not read over ssh"
grep -q 'mpe-device:fake-host-1' "$FAKE_LOG_DIR/gh.db" || fail "default device id not used"
ok "environment target and default device id"

echo "all passed"
