# Local laptop vs nerdrack — MPE-Module dev environment

*Last updated: 2026-08-15 16:08 (America/Toronto)*

**Purpose:** Where guardrails and MCP wiring live, and how to set up each environment. The laptop is for normal Cursor/Claude work (manager / Mitch); nerdrack is for unattended **Claude Code** YOLO builds.

---

## Decision summary

| Topic | Laptop (local) | Nerdrack (headless YOLO) |
|---|---|---|
| **Headless marker** | **None** — no `.claude/settings.local.json` | **Required** — copy from example; enforced by `check-guardrails.sh` |
| **Runner** | Cursor or interactive Claude | **`claude-yolo.sh`** → `claude --dangerously-skip-permissions` |
| **Guardrails** | Normal project rules in `AGENTS.md` | agentjail + yolo-shell-guard + project PreToolUse hooks |
| **`.claude/mcp.json`** | Optional / not required for Pi work | Gitignored; copy from `.claude/mcp.json.headless.example` |
| **OneCLI agent** | Your laptop agent (if any) | **IO MPE Module** (dashboard name; env on box) |
| **GitHub MCP secret** | gh CLI / laptop auth | `github-mpe-module` |
| **Pi access** | `mpe` CLI over LAN | **Blocked** — Pi not reachable from VPS |
| **Backpressure** | `python3 -m unittest discover -s tests -q` | Same — nerdrack runs unit tests only |

**Rationale:** MPE is an appliance project. Nerdrack can implement and test Python/unit logic; Pi soak, deploy scripts, and audio/systemd changes stay human gates on the laptop.

---

## Laptop setup (Mitch / manager)

### 1. Clone and tests

```bash
git clone git@github.com:MitchSchwartz/MPE-Sound-Module.git MPE-Module
cd MPE-Module
git checkout dev
python3 -m unittest discover -s tests -q
```

### 2. Do **not** install headless config

- **Do not** copy `settings.local.json.headless.example` → `.claude/settings.local.json` on the laptop.
- Confirm absent: `test ! -f .claude/settings.local.json && echo OK`

### 3. Queue a task (after Gate A)

```bash
bash scripts/yolo/enqueue-yolo-task.sh add \
  --id my-task \
  --branch yolo/my-task \
  --spec specs/my-task.md \
  --skills incremental-implementation,test-driven-development \
  --prompt "Implement per approved spec; open PR to dev."

bash scripts/yolo/enqueue-yolo-task.sh approve --id my-task
git push   # queue file + branch must be on origin for nerdrack
```

Human gates (when needed): `pi_soak`, `systemd_change`, `audio_profile`, `mpe_env` — clear with `clear-gate` after Mitch approval.

---

## Nerdrack setup (Claude Code YOLO)

**Host:** `claudeLogin` (`claude-sandbox@167.160.187.172`)  
**SSH alias (laptop):** `claude-yolo-mpe` → runs `claude-yolo.sh` in repo

### 1. Clone (first time)

```bash
ssh claudeLogin
cd ~/workspace
git clone git@github.com:MitchSchwartz/MPE-Sound-Module.git MPE-Module
cd MPE-Module
git checkout dev
git pull
```

### 2. OneCLI env (Mitch gate — once per box)

Create `~/.onecli/mpe-module.env` (mode `600`):

```bash
ONECLI_AOC_TOKEN=<aoc_* from IO MPE Module agent in OneCLI dashboard>
GITHUB_MCP_SECRET=github-mpe-module
YOLO_REPO=MitchSchwartz/MPE-Sound-Module
NTFY_TOPIC=<secret ntfy topic>
```

In OneCLI dashboard, secret `github-mpe-module`:

- Host: `api.github.com`
- Header: `Authorization`
- Format: `Bearer {value}` (GitHub PAT with repo access to `MPE-Sound-Module`)

Health check:

```bash
bash scripts/check-onecli-github.sh github-mpe-module
```

### 3. Bootstrap headless config

```bash
bash scripts/yolo/bootstrap-nerdrack.sh
```

This copies `.claude/mcp.json` and `.claude/settings.local.json` from examples and runs gate checks.

### 4. Run a queued task

```bash
cd ~/workspace/MPE-Module
git pull
git checkout yolo/my-task
export YOLO_TASK_ID=my-task
bash scripts/yolo/claude-yolo.sh -p "Your task prompt"
```

Or from laptop:

```bash
ssh claude-yolo-mpe
# inside repo after export YOLO_TASK_ID=...
```

Bulk drain: `bash scripts/yolo/run-yolo-queue.sh`

---

## Protected branches

| Branch | Role |
|--------|------|
| `main` | Stable appliance line — Pi default |
| `dev` | Integration — **PR target for agent work** |
| `yolo/<task>` | Agent working branches |

Shell guard blocks direct push and force-push to `main` and `dev`.

---

## What nerdrack must never do

- `scripts/deploy-all.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`
- `mpe restart` or Pi `ssh`/`scp`/`rsync`
- `sudo apt` on appliance/host
- Merge PRs without independent review pass (honor system until branch protection)

Pi validation stays on laptop: `mpe ping`, checkout branch on Pi, ear tests (B2/B10) are Mitch-only.
