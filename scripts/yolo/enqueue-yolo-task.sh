#!/usr/bin/env bash
# Laptop-side: queue tasks for nerdrack after sync human gates (Gate A, Mitch-only gates).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_project.sh"

QUEUE="$ROOT/.claude/primitives/yolo-queue.json"

usage() {
  cat <<'EOF'
Usage:
  enqueue-yolo-task.sh add --id ID --branch BRANCH [--spec PATH] [--skills a,b] [--prompt TEXT]
      [--gate pi_soak] [--gate systemd_change] ...
  enqueue-yolo-task.sh approve --id ID [--by NAME]
  enqueue-yolo-task.sh clear-gate --id ID --gate GATE_NAME
  enqueue-yolo-task.sh list
  enqueue-yolo-task.sh mark-done --id ID
  enqueue-yolo-task.sh remove --id ID

Human gates (require Mitch on laptop before nerdrack):
  pi_soak, systemd_change, audio_profile, mpe_env

Flow:
  1. Laptop: spec + spec-review → Mitch Gate A → spec Status: Approved
  2. enqueue-yolo-task.sh add ...
  3. enqueue-yolo-task.sh approve --id ...   (sets spec_approved + status ready)
  4. Nerdrack: YOLO_TASK_ID=... scripts/yolo/claude-yolo.sh -p "..."

Bulk: run-yolo-queue.sh drains all ready tasks in queue order.
EOF
}

cmd="${1:-}"
shift || true

python3 - "$ROOT" "$QUEUE" "$cmd" "$@" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

root = Path(sys.argv[1])
queue_path = Path(sys.argv[2])
cmd = sys.argv[3]
args = sys.argv[4:]

HUMAN_GATES = ("pi_soak", "systemd_change", "audio_profile", "mpe_env")


def now_toronto():
    import os
    import subprocess
    try:
        return subprocess.check_output(
            ["date", "+%Y-%m-%d %H:%M"],
            text=True,
            env={**os.environ, "TZ": "America/Toronto"},
        ).strip()
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M")


def load_queue():
    if queue_path.is_file():
        with open(queue_path, encoding="utf-8") as f:
            return json.load(f)
    return {"_README": "See enqueue-yolo-task.sh", "tasks": []}


def save_queue(data):
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def parse_flags(argv):
    out = {"gates": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--id", "--branch", "--spec", "--skills", "--prompt", "--by", "--gate"):
            key = a[2:].replace("-", "_")
            i += 1
            if i >= len(argv):
                sys.exit(f"Missing value for {a}")
            if key == "gate":
                out["gates"].append(argv[i])
            elif key == "skills":
                out[key] = [s.strip() for s in argv[i].split(",") if s.strip()]
            else:
                out[key] = argv[i]
        else:
            sys.exit(f"Unknown argument: {a}")
        i += 1
    return out


def find_task(tasks, task_id):
    for t in tasks:
        if t.get("id") == task_id:
            return t
    return None


data = load_queue()
tasks = data.setdefault("tasks", [])

if cmd == "list":
    if not tasks:
        print("(queue empty)")
        sys.exit(0)
    for t in tasks:
        gates = t.get("human_gates") or {}
        pending = [g for g, ok in gates.items() if not ok]
        print(
            f"{t.get('id')}\t{t.get('status', 'draft')}\tbranch={t.get('branch', '—')}"
            f"\tspec_approved={t.get('spec_approved', False)}"
            + (f"\tpending_gates={','.join(pending)}" if pending else "")
        )
    sys.exit(0)

if cmd == "add":
    f = parse_flags(args)
    for req in ("id", "branch"):
        if req not in f:
            sys.exit(f"add requires --{req}")
    if find_task(tasks, f["id"]):
        sys.exit(f"Task id already exists: {f['id']}")
    required_gates = f.get("gates", [])
    for g in required_gates:
        if g not in HUMAN_GATES:
            sys.exit(f"Unknown gate {g!r}. Valid: {', '.join(HUMAN_GATES)}")
    human_gates = {g: (g not in required_gates) for g in HUMAN_GATES}
    entry = {
        "id": f["id"],
        "branch": f["branch"],
        "status": "draft",
        "spec": f.get("spec"),
        "skills": f.get("skills", []),
        "prompt": f.get("prompt"),
        "spec_approved": False,
        "required_gates": required_gates,
        "human_gates": human_gates,
        "created_at": now_toronto(),
    }
    tasks.append(entry)
    save_queue(data)
    print(f"Queued {f['id']} (status=draft). Run approve after Gate A on laptop.")
    sys.exit(0)

if cmd == "approve":
    f = parse_flags(args)
    if "id" not in f:
        sys.exit("approve requires --id")
    t = find_task(tasks, f["id"])
    if not t:
        sys.exit(f"Unknown task id: {f['id']}")
    t["spec_approved"] = True
    t["approved_at"] = now_toronto()
    t["approved_by"] = f.get("by", "mitch")
    blocked = [
        g for g in (t.get("required_gates") or [])
        if not (t.get("human_gates") or {}).get(g)
    ]
    if blocked:
        sys.exit(f"Cannot approve: clear Mitch gates first: {', '.join(blocked)}")
    t["status"] = "ready"
    save_queue(data)
    print(f"Task {f['id']} is ready for nerdrack (YOLO_TASK_ID={f['id']}).")
    sys.exit(0)

if cmd == "clear-gate":
    f = parse_flags(args)
    if "id" not in f or "gate" not in f:
        sys.exit("clear-gate requires --id and --gate")
    t = find_task(tasks, f["id"])
    if not t:
        sys.exit(f"Unknown task id: {f['id']}")
    hg = t.setdefault("human_gates", {g: False for g in HUMAN_GATES})
    if f["gate"] not in HUMAN_GATES:
        sys.exit(f"Unknown gate {f['gate']!r}")
    hg[f["gate"]] = True
    t.setdefault("required_gates", [])
    if f["gate"] not in t["required_gates"]:
        t["required_gates"].append(f["gate"])
    save_queue(data)
    print(f"Cleared gate {f['gate']} on {f['id']}.")
    sys.exit(0)

if cmd == "mark-done":
    f = parse_flags(args)
    if "id" not in f:
        sys.exit("mark-done requires --id")
    t = find_task(tasks, f["id"])
    if not t:
        sys.exit(f"Unknown task id: {f['id']}")
    t["status"] = "done"
    t["completed_at"] = now_toronto()
    save_queue(data)
    print(f"Marked {f['id']} done.")
    sys.exit(0)

if cmd == "remove":
    f = parse_flags(args)
    if "id" not in f:
        sys.exit("remove requires --id")
    before = len(tasks)
    data["tasks"] = [t for t in tasks if t.get("id") != f["id"]]
    if len(data["tasks"]) == before:
        sys.exit(f"Unknown task id: {f['id']}")
    save_queue(data)
    print(f"Removed {f['id']}.")
    sys.exit(0)

sys.exit("Unknown command. Use: add | approve | clear-gate | list | mark-done | remove")
PY
