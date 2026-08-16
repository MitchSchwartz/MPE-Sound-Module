#!/usr/bin/env bash
# Gate: nerdrack YOLO sessions must match an approved queue entry (sync gates on laptop first).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

QUEUE="$ROOT/.claude/primitives/yolo-queue.json"
TASK_ID="${YOLO_TASK_ID:-}"

is_nerdrack() {
  [[ -f "$ROOT/.claude/settings.local.json" ]] || [[ -f "$ROOT/.yolo/headless" ]]
}

if ! is_nerdrack; then
  echo "== check-yolo-gates =="
  echo "  laptop mode: skipped (no headless marker)"
  exit 0
fi

echo "== check-yolo-gates =="

if [[ -z "$TASK_ID" ]]; then
  echo "FAIL: YOLO_TASK_ID is required on nerdrack." >&2
  echo "  Enqueue on laptop: scripts/yolo/enqueue-yolo-task.sh add ..." >&2
  echo "  Then approve gates: scripts/yolo/enqueue-yolo-task.sh approve --id <id>" >&2
  exit 1
fi

if [[ ! -f "$QUEUE" ]]; then
  echo "FAIL: missing $QUEUE" >&2
  exit 1
fi

python3 - "$QUEUE" "$TASK_ID" "$ROOT" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

queue_path, task_id, root = sys.argv[1:4]
root = Path(root)

with open(queue_path, encoding="utf-8") as f:
    data = json.load(f)

tasks = data.get("tasks") or []
entry = next((t for t in tasks if t.get("id") == task_id), None)
if not entry:
    known = ", ".join(t.get("id", "?") for t in tasks) or "(empty queue)"
    print(f"FAIL: YOLO_TASK_ID={task_id!r} not in yolo-queue.json.", file=sys.stderr)
    print(f"  Known task ids: {known}", file=sys.stderr)
    sys.exit(1)

status = entry.get("status", "draft")
if status != "ready":
    print(f"FAIL: task {task_id!r} status is {status!r}, expected 'ready'.", file=sys.stderr)
    print("  Laptop: scripts/yolo/enqueue-yolo-task.sh approve --id", task_id, file=sys.stderr)
    sys.exit(1)

if not entry.get("spec_approved"):
    print(f"FAIL: task {task_id!r} — spec_approved is false (Gate A not cleared on laptop).", file=sys.stderr)
    sys.exit(1)

human_gates = entry.get("human_gates") or {}
blocked = [name for name, cleared in human_gates.items() if not cleared]
if blocked:
    print(f"FAIL: task {task_id!r} — Mitch gates not cleared: {', '.join(blocked)}", file=sys.stderr)
    print("  Clear on laptop after Mitch approval, then re-approve the task.", file=sys.stderr)
    sys.exit(1)

spec_rel = entry.get("spec")
if spec_rel:
    spec_path = root / spec_rel
    if not spec_path.is_file():
        print(f"FAIL: spec file missing: {spec_rel}", file=sys.stderr)
        sys.exit(1)
    text = spec_path.read_text(encoding="utf-8")
    if not re.search(r"(?im)^\*?Status:\*?\s*Approved\b", text):
        print(f"FAIL: {spec_rel} does not contain 'Status: Approved'.", file=sys.stderr)
        print("  Update spec status on laptop after Gate A, then enqueue approve.", file=sys.stderr)
        sys.exit(1)

branch = entry.get("branch")
if branch:
    import subprocess
    current = subprocess.check_output(
        ["git", "-C", str(root), "branch", "--show-current"], text=True
    ).strip()
    if current != branch:
        print(f"FAIL: on branch {current!r}, task expects {branch!r}.", file=sys.stderr)
        sys.exit(1)

print(f"  task id: {task_id}")
print(f"  status: ready")
if spec_rel:
    print(f"  spec: {spec_rel} (Approved)")
if entry.get("skills"):
    print(f"  skills: {entry['skills']}")
print("All YOLO gates passed.")
PY
