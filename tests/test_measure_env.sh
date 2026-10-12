#!/bin/bash
# scripts/lib/measure-env.sh: measurement edits to /etc/mpe/mpe.env happen only
# when they change something, and the exit trap restores the file byte for byte
# (2026-10-12). Runs against a temp file; never touches /etc.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIB="$ROOT/scripts/lib/measure-env.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

ok() {
    echo "OK: $*"
}

_fixture() {
    # Odd bytes on purpose: a comment, a quoted value, no trailing newline.
    printf '# appliance env\nMPE_JACK_SOFTMODE=1\nMPE_POLY_GOVERNOR=1\nMPE_AUDIO_OUTPUT_LABEL="FiiO KA1"\nMPE_JACK_BUFFER=96' >"$1"
    chmod 0640 "$1"
}

# Run a snippet in a child bash with the helper sourced; prints its stdout.
_child() {
    MPE_MEASURE_ENV_FILE="$1" RESTORE_LOG="$TMP/restore.log" bash -c "
        set -euo pipefail
        source '$LIB'
        $2
    "
}

# 1. No write when the config is unchanged: same inode, same mtime, no restore work.
F="$TMP/unchanged.env"
_fixture "$F"
cp -p "$F" "$TMP/unchanged.orig"
before="$(stat -c '%i %Y %a' "$F")"
touch -d '2001-01-01 00:00:00' "$F"
before="$(stat -c '%i %Y %a' "$F")"
: >"$TMP/restore.log"
out="$(_child "$F" '
    _note() { echo restored >>"$RESTORE_LOG"; }
    mpe_env_arm
    mpe_env_set MPE_JACK_SOFTMODE 1
    echo "changed=$MPE_ENV_LAST_CHANGED"
    if [ "$MPE_ENV_LAST_CHANGED" = 1 ]; then mpe_env_on_restore _note; fi
')"
[ "$out" = "changed=0" ] || fail "unchanged value reported as a change: $out"
[ "$(stat -c '%i %Y %a' "$F")" = "$before" ] || fail "file was rewritten for an unchanged value"
[ ! -s "$TMP/restore.log" ] || fail "restore work ran although nothing changed"
cmp -s "$F" "$TMP/unchanged.orig" || fail "bytes differ after a no-op"
ok "no write and no restart when the requested value is already live"

# 2. A change is written, then restored byte for byte on a normal exit.
F="$TMP/changed.env"
_fixture "$F"
cp -p "$F" "$TMP/changed.orig"
: >"$TMP/restore.log"
_child "$F" '
    _note() { echo "restored $(grep ^MPE_JACK_SOFTMODE= "$MPE_MEASURE_ENV_FILE")" >>"$RESTORE_LOG"; }
    mpe_env_arm
    mpe_env_set MPE_JACK_SOFTMODE 0
    [ "$MPE_ENV_LAST_CHANGED" = 1 ] || exit 9
    mpe_env_on_restore _note
    mpe_env_on_restore _note
    mpe_env_set MPE_POLY_CEILING 64
    grep -q "^MPE_JACK_SOFTMODE=0$" "$MPE_MEASURE_ENV_FILE" || exit 8
    grep -q "^MPE_POLY_CEILING=64$" "$MPE_MEASURE_ENV_FILE" || exit 7
' 2>/dev/null || fail "edit step failed (rc=$?)"
cmp -s "$F" "$TMP/changed.orig" || fail "file not byte-identical after exit"
[ "$(stat -c '%a' "$F")" = 640 ] || fail "mode changed to $(stat -c '%a' "$F")"
[ "$(cat "$TMP/restore.log")" = "restored MPE_JACK_SOFTMODE=1" ] \
    || fail "restore hook must run once, after the file is back: $(cat "$TMP/restore.log")"
ok "edits restored byte for byte, restart hook once and after the restore"

# 3. Restore on error: set -e failure mid-run.
F="$TMP/error.env"
_fixture "$F"
cp -p "$F" "$TMP/error.orig"
rc=0
_child "$F" '
    mpe_env_arm
    mpe_env_set MPE_POLY_GOVERNOR 0
    mpe_env_set MPE_NEW_KEY 1
    false
    echo unreachable
' >/dev/null 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "error exit status was swallowed"
cmp -s "$F" "$TMP/error.orig" || fail "file not restored after an error exit"
ok "restored after an error (rc=$rc kept)"

# 4. Restore on SIGTERM, with the script's own hook running first.
F="$TMP/term.env"
_fixture "$F"
cp -p "$F" "$TMP/term.orig"
: >"$TMP/restore.log"
rc=0
_child "$F" '
    _hook() { echo "hook rc=$?" >>"$RESTORE_LOG"; }
    mpe_env_arm _hook
    mpe_env_set MPE_JACK_SOFTMODE 0
    kill -TERM $$
    sleep 5
    echo unreachable
' >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 143 ] || fail "SIGTERM exit status $rc, expected 143"
cmp -s "$F" "$TMP/term.orig" || fail "file not restored after SIGTERM"
grep -q '^hook rc=143$' "$TMP/restore.log" || fail "script hook did not see the exit status: $(cat "$TMP/restore.log")"
ok "restored after SIGTERM; script hook ran first and saw rc=143"

# 5. No edit without the trap.
F="$TMP/unarmed.env"
_fixture "$F"
cp -p "$F" "$TMP/unarmed.orig"
rc=0
_child "$F" 'mpe_env_set MPE_JACK_SOFTMODE 0' >/dev/null 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "mpe_env_set wrote without mpe_env_arm"
cmp -s "$F" "$TMP/unarmed.orig" || fail "unarmed set changed the file"
ok "mpe_env_set refuses to edit before the restore trap is armed"

# 6. A tool that edits the file itself (set-surge-audio.sh) is covered by an
#    explicit snapshot taken before it runs.
F="$TMP/external.env"
_fixture "$F"
cp -p "$F" "$TMP/external.orig"
_child "$F" '
    mpe_env_arm
    mpe_env_snapshot
    sed -i "s/^MPE_JACK_BUFFER=.*/MPE_JACK_BUFFER=1024/" "$MPE_MEASURE_ENV_FILE"
' 2>/dev/null
cmp -s "$F" "$TMP/external.orig" || fail "external edit after mpe_env_snapshot not restored"
ok "snapshot before an external editor restores its edit too"

# 7. One authority: no measure script that loads patches or plays the MIDI
#    generator keeps a private env writer.
offenders=()
for s in "$ROOT"/scripts/measure-*.sh; do
    if grep -qE 'load-patch-osc|mpe_load_patch_osc|midi-load(-hold)?\.py' "$s" \
        && grep -qE '^_set_env_var\(\)|install -m [0-9]+ "\$tmp" "\$ENV_FILE"|sed -i .*\$ENV_FILE' "$s"; then
        offenders+=("$(basename "$s")")
    fi
done
[ "${#offenders[@]}" -eq 0 ] || fail "private env writers left in: ${offenders[*]}"
ok "harnesses sharing the loader or generator edit mpe.env only via lib/measure-env.sh"

echo "test_measure_env.sh: all checks passed"
