#!/usr/bin/env bash
# Install the PreToolUse hook scripts OUTSIDE the repo, at a stable absolute
# path, and point .claude/settings.local.json at them.
#
#   ./scripts/yolo/install-hooks-outside-repo.sh            # install + rewrite config
#   ./scripts/yolo/install-hooks-outside-repo.sh --check    # report drift, change nothing
#
# WHY THIS EXISTS (measured 2026-08-16, twice, on two branches)
#
#   The hook config shipped a RELATIVE command:
#       "command": "bash scripts/yolo/yolo-shell-guard.sh"
#
#   Two branches — apc-faders-loop-mix and yolo/shutdown-timing-fix — were cut
#   before scripts/yolo/ existed on main. Checking either one out deletes the
#   guard from the working tree, and the hook then fails with:
#
#       PreToolUse:Bash hook error
#       Failed with non-blocking status code: ... No such file or directory
#
#   A PreToolUse hook that errors does NOT deny — it is skipped and the Bash
#   call proceeds. So a routine branch checkout silently disables every guard
#   rule for every agent sharing that checkout. This is not hypothetical: an
#   agent ran a full feature implementation on Racknerd in exactly that state.
#
#   Installing outside the repo makes the guard independent of the checked-out
#   branch. $HOME keeps it portable across the laptop and Racknerd, which have
#   different repo paths.
#
# This is a RELIABILITY fix, not a containment one. Per the decision recorded in
# docs/racknerd-pi-access-spec.md (§Decision C), the appliance is expendable and
# the Tailscale ACL is the only real control. The guard is mistake-prevention —
# but mistake-prevention that vanishes on a branch switch is not even that.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/scripts/yolo"
DEST="$HOME/.claude/hooks"
SETTINGS="$ROOT/.claude/settings.local.json"

HOOKS=(yolo-shell-guard.sh agentjail-hook-wrapper.sh)

CHECK=false
[ "${1:-}" = "--check" ] && CHECK=true

drift=0

if [ "$CHECK" = true ]; then
    echo "Checking installed hooks against the repo copies ..."
    for h in "${HOOKS[@]}"; do
        if [ ! -f "$DEST/$h" ]; then
            echo "  MISSING:   $DEST/$h"
            drift=1
        elif ! cmp -s "$SRC/$h" "$DEST/$h"; then
            echo "  DRIFT:     $DEST/$h differs from $SRC/$h"
            drift=1
        else
            echo "  ok:        $h"
        fi
    done
    if [ -f "$SETTINGS" ] && grep -q '"command": "bash scripts/yolo/' "$SETTINGS" 2>/dev/null; then
        echo "  RELATIVE:  $SETTINGS still uses repo-relative hook paths"
        drift=1
    fi
    [ "$drift" -eq 0 ] && echo "  no drift"
    exit "$drift"
fi

mkdir -p "$DEST"
for h in "${HOOKS[@]}"; do
    if [ ! -f "$SRC/$h" ]; then
        echo "ERROR: $SRC/$h not found. You are probably on a branch cut before" >&2
        echo "       scripts/yolo/ existed — check out dev and re-run." >&2
        exit 1
    fi
    install -m 0755 "$SRC/$h" "$DEST/$h"
    echo "  installed: $DEST/$h"
done

if [ ! -f "$SETTINGS" ]; then
    echo ""
    echo "No $SETTINGS — nothing to rewrite."
    echo "Point your PreToolUse Bash hooks at:"
    for h in "${HOOKS[@]}"; do echo "  bash \"\$HOME/.claude/hooks/$h\""; done
    exit 0
fi

cp -p "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
python3 - "$SETTINGS" <<'PY'
import json, re, sys
p = sys.argv[1]
raw = open(p).read()
new = re.sub(
    r'"command"\s*:\s*"bash (?:\./)?scripts/yolo/([A-Za-z0-9._-]+)"',
    r'"command": "bash \\"$HOME/.claude/hooks/\1\\""',
    raw,
)
json.loads(new)  # fail loudly rather than write invalid JSON
open(p, 'w').write(new)
print("  rewrote hook commands to $HOME/.claude/hooks/ (backup alongside)")
PY

echo ""
echo "Done. Verify the guard still denies:"
echo "  printf '%s' '{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"mpe restart surge\"}}' \\"
echo "    | YOLO_HOOK_AGENT=claude bash \"\$HOME/.claude/hooks/yolo-shell-guard.sh\""
echo ""
echo "Re-run after changing the guard in the repo — the installed copy is a"
echo "snapshot. './scripts/yolo/install-hooks-outside-repo.sh --check' reports drift."
