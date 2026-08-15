#!/usr/bin/env bash
# Drain ready tasks from yolo-queue.json on nerdrack (bulk Claude YOLO).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

QUEUE="$ROOT/.claude/primitives/yolo-queue.json"
CLAUDE_YOLO="$ROOT/scripts/yolo/claude-yolo.sh"

is_nerdrack() {
  [[ -f "$ROOT/.claude/settings.local.json" ]] || [[ -f "$ROOT/.yolo/headless" ]]
}

if ! is_nerdrack; then
  echo "run-yolo-queue.sh is for nerdrack only (requires headless config)." >&2
  echo "On laptop, use enqueue-yolo-task.sh to queue; SSH to nerdrack to drain." >&2
  exit 1
fi

mapfile -t READY_IDS < <(
  python3 - "$QUEUE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
for t in data.get("tasks") or []:
    if t.get("status") == "ready":
        print(t["id"])
PY
)

if [[ ${#READY_IDS[@]} -eq 0 ]]; then
  echo "No ready tasks in queue."
  exit 0
fi

echo "Draining ${#READY_IDS[@]} ready task(s): ${READY_IDS[*]}"

for task_id in "${READY_IDS[@]}"; do
  PROMPT="$(python3 - "$QUEUE" "$task_id" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
t = next(x for x in data["tasks"] if x["id"] == sys.argv[2])
skills = t.get("skills") or []
print(t.get("prompt") or f"Run queued task {t['id']} with skills: {', '.join(skills)}")
PY
)"
  SKILLS="$(python3 - "$QUEUE" "$task_id" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
t = next(x for x in data["tasks"] if x["id"] == sys.argv[2])
print(",".join(t.get("skills") or []))
PY
)"
  BRANCH="$(python3 - "$QUEUE" "$task_id" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
t = next(x for x in data["tasks"] if x["id"] == sys.argv[2])
print(t.get("branch") or "")
PY
)"

  echo "== Task: $task_id (branch: $BRANCH) =="
  if [[ -n "$BRANCH" ]]; then
    git -C "$ROOT" checkout "$BRANCH"
    git -C "$ROOT" pull --ff-only origin "$BRANCH" 2>/dev/null || true
  fi

  export YOLO_TASK_ID="$task_id"
  export YOLO_SKILLS="$SKILLS"
  export YOLO_TASK="$task_id"

  if YOLO_TASK_ID="$task_id" YOLO_SKILLS="$SKILLS" YOLO_TASK="$task_id" \
    bash "$CLAUDE_YOLO" -p "$PROMPT"; then
    bash "$ROOT/scripts/yolo/enqueue-yolo-task.sh" mark-done --id "$task_id"
  else
    echo "Task $task_id failed — queue drain stopped." >&2
    exit 1
  fi
done

echo "Queue drain complete."
