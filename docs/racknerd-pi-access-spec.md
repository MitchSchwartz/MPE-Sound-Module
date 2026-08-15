# Racknerd YOLO → MPE Pi access (Tailscale + command containment)

**Status:** Draft — Gate A (Mitch approval) required before implementation  
**Created:** 2026-08-15 (America/Toronto)  
**Last updated:** 2026-08-15 17:39 (America/Toronto)

**Related:** [`local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) · [`AGENTS.md`](../AGENTS.md) · OM-Repo [`Docs/appliance-cli-pattern.md`](https://github.com/opsMachine/OM-Repo/blob/main/Docs/appliance-cli-pattern.md) · [`mpe-cli`](https://github.com/MitchSchwartz/mpe-cli)

**Review audit:** 2026-08-15 — spec revised against code (`yolo-shell-guard.sh`, `check-yolo-gates.sh`, `mpe-cli`). Do **not** approve until Layer 2 ↔ 3 seam (token dispatch) is reflected here and in Phase deliverables.

---

## Problem

Today the Racknerd VPS runs MPE YOLO builds but **cannot reach the Pi** (LAN-only, blocked by `yolo-shell-guard.sh`). Unit tests on the VPS miss JACK, audio, and appliance integration. Mitch runs Pi deploy and soak from the laptop.

We want the YOLO agent to **run Pi tests** (Phase A) and optionally **bounded deploy** (Phase B) after merge — without:

- exposing the home LAN to the VPS,
- giving the agent an interactive shell on the Pi,
- or bypassing existing human gates for destructive appliance changes.

---

## Goals

| # | Goal |
|---|------|
| G1 | Racknerd can reach **only** the MPE Pi over Tailscale (SSH port 22). |
| G2 | YOLO can run **allowlisted** `mpe` subcommands (read + `test pi …`) from Racknerd. |
| G3 | Pi SSH for the agent identity uses **forced command** — no general shell. |
| G4 | Home network and other tailnet nodes stay **unreachable** from Racknerd. |
| G5 | Deploy-class actions remain behind **`pi_soak`** (and related) queue gates. |
| G6 | Defense in depth: Tailscale ACL + Pi SSH + `mpe-cli` + `yolo-shell-guard`. |

**Layer weight (important):** Layers **1–2** (Tailscale ACL, forced-command wrapper) are **containment** — they hold even if Racknerd is compromised. Layers **3–4** (`mpe-cli`, shell guard) are **ergonomics and mistake prevention** on the agent host; a compromised Racknerd can rewrite them. Do not treat four layers as equal strength.

## Non-goals

- Replacing Mitch's full admin path (`mitch@pi`, laptop `mpe` with restart/deploy).
- Subnet routing from Pi into `192.168.x.x` for the agent path.
- Automated ear tests (B2/B10) or promotion `dev` → `main` on the appliance.
- Running Surge/JACK **on** Racknerd (tests execute **on** the Pi).

---

## Policy authority (single source of truth)

| Policy | Owner | Spec role |
|---|---|---|
| Test suite names, log line cap, log unit enum | **`mpe-cli`** (`lib/test_suites.sh`, `lib/validate.sh`, `commands/logs.sh`) | Reference only — link, do not restate with ellipsis |
| Phase B branch allowlist (`dev`, `yolo/*`) | **`mpe-cli`** (`commands/looper.sh` + new enum validator) | Specify intent; implementation in CLI repo |
| Token → fixed command map | **Pi** `mpe-yolo-remote.sh` | List example tokens; map lives on Pi |
| Racknerd bash allow/deny | **`yolo-shell-guard.sh`** | List probe cases; regexes live in repo |
| Queue human gates | **`check-yolo-gates.sh`** + session gate file (new) | Gate names only |

When this spec and code disagree, **code wins until spec is updated** — never the reverse.

---

## Threat model

| Threat | Mitigation layer |
|---|---|
| Compromised Racknerd scans home LAN | Tailscale ACL: Pi:22 only; **no** subnet routes |
| Compromised Racknerd pivots to other tailnet hosts | ACL deny racknerd-yolo → all except `mpe-pi:22` |
| Agent obtains interactive Pi shell | Forced-command SSH user; `no-pty`; wrapper rejects stdin |
| Agent runs `deploy-all.sh`, audio profile, systemd | Guard deny + forced-command deny + queue gates |
| Agent runs raw `ssh`/`scp`/`rsync` | Guard deny **any** raw ssh/scp/rsync (not hostname-specific) |
| YOLO `--dangerously-skip-permissions` | Narrow allowlist; no passthrough args to remote shell |
| Compromised Racknerd rewrites guard or mpe-cli | **Not containable** at L3/L4 — rely on L1/L2 |
| Agent edits `~/.config/mpe/mpe-yolo.env` (source = code exec) | Root-owned env (0444); guard deny writes under `.config/mpe/` |
| Agent deploys branch then runs `mpe test pi` (Phase B) | Agent-authored unittest runs as `mitch` on Pi — see Phase B mitigations |
| Partial test run reports success (exit 3) | YOLO smoke treats exit 3 as failure; never `--allow-partial` on Racknerd |

---

## Architecture

```text
┌──────────────── Racknerd VPS ─────────────────────────────┐
│  OS user: claude-sandbox (YOLO runtime — name explicitly)  │
│  claude-yolo.sh → check-yolo-gates.sh (writes session      │
│    gate file) → yolo-shell-guard (deny raw ssh; mpe       │
│    allowlist) → mpe-cli (token dispatch via SSH argv)       │
└───────────────────────┬───────────────────────────────────┘
                        │ Tailscale (ACL: mpe-pi:22 only)
                        ▼
┌──────────────── MPE Pi ───────────────────────────────────┐
│  sshd → mpe-yolo user → mpe-yolo-remote.sh (token map)   │
│       → sudo -u mitch (narrow sudoers) → fixed commands     │
└─────────────────────────────────────────────────────────────┘

Mitch (laptop): mitch@pi — normal shell, separate key, unchanged.
```

### Layer 2 ↔ 3 seam (load-bearing)

**Today:** `mpe-cli` ships work via `mpe_cli_remote_bash` → `ssh … bash -s` with a **heredoc on stdin** (`lib/ssh.sh`). Under a forced-command key, `SSH_ORIGINAL_COMMAND` is `bash -s`; the real payload is stdin.

**Required:** YOLO profile must use **token dispatch** — `ssh mpe-yolo@mpe-pi test-pi-audio` (argv token only, **no stdin heredoc**). The Pi wrapper maps tokens to fixed local commands. This adapter is an explicit **Phase 3 deliverable** in `mpe-cli`; it does not exist today.

---

## Layer 1 — Tailscale (network containment)

### Pi

- Join tailnet with stable name **`mpe-pi`** (or keep `raspberrypi2` until renamed — guard must not rely on hostname substring either way).
- **Do not advertise LAN subnet routes** to the tailnet for this use case.
  If routes exist for other reasons, ACL must block racknerd-yolo from using them.
- Optional: `tailscale set --shields-up=true` if Pi-initiated connections are not needed.

### Racknerd

- Tailscale installed; node tagged **`tag:racknerd-yolo`** (dedicated identity).
- Not shared with Mitch's personal tailnet login where avoidable.

### ACL policy (requirements)

| Rule | Intent |
|---|---|
| `tag:racknerd-yolo` → `tag:mpe-pi`:22 | Agent path only |
| `tag:racknerd-yolo` → * (deny) | No other tailnet nodes |
| `tag:racknerd-yolo` → subnet routes (deny) | No LAN pivot via Pi |
| Mitch laptop → `tag:mpe-pi`:22 | Admin unchanged |

**Verification (manual, Phase 1 exit):**

```bash
# From Racknerd — connectivity (supplementary; not ACL proof alone)
tailscale ping mpe-pi

# From Racknerd — TCP ACL for agent path (primary)
nc -zv mpe-pi 22          # must succeed
nc -zv mpe-pi 8096        # or other non-22 port — must fail (ACL)

# From Racknerd — LAN / other tailnet (must fail)
ping 192.168.1.210
curl http://mediacenter:8096

# Pi SSH auth (separate from ACL — wrong identity/key)
ssh mitch@mpe-pi          # must fail (wrong user/key for agent path)
# After Layer 3:
MPE_CLI_CONFIG=~/.config/mpe/mpe-yolo.env mpe ping   # must succeed
```

---

## Layer 2 — Pi SSH (no agent shell)

### Two identities — separate keys

| Identity | OS user | Key location | Access |
|---|---|---|---|
| **Mitch (admin)** | `mitch` | Laptop | Full shell (LAN or tailnet) |
| **YOLO (agent)** | `mpe-yolo` | Racknerd only (`claude-sandbox`) | Forced command only |

### Forced command

`/home/mpe-yolo/.ssh/authorized_keys` (example):

```text
command="/usr/local/sbin/mpe-yolo-remote.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,no-user-rc ssh-ed25519 AAAA... racknerd-yolo
```

### `mpe-yolo-remote.sh` (root-owned, 755, not writable by `mpe-yolo`)

**Contract:**

1. Reads **one token** from `SSH_ORIGINAL_COMMAND` / wrapper argv (e.g. `test-pi-audio`, `ping`, `status`, `logs-surge-50`) — **not** from stdin.
2. **Rejects stdin** — if stdin is not empty / not a TTY with no piped payload, exit 1 and log. Prevents `bash -s` heredoc bypass.
3. Maps token → fixed local command (no user-supplied shell fragments).
4. Runs via **`sudo -u mitch -H`** only for predefined operations:
   - **Read tokens:** fixed read-only scripts or systemd journal tails as documented in token map.
   - **`test-pi-<suite>` tokens:** run `python3 -m unittest …` in `~/MPE-Module` via a **fixed suite→module list** (same registry as `mpe-cli` — not agent-supplied paths).
   - **Phase B deploy token:** `configure-pi-paths.sh` + `git checkout` for **enum-validated branch only** (see Layer 3).
5. Logs every invocation (timestamp, token, exit code) to `/var/log/mpe-yolo-remote.log` (add logrotate; no unbounded growth).
6. Rejects unknown tokens with exit 1.

### sudoers (Mitch-only gate — specify before Phase 2)

Add a **narrow** sudoers drop-in (not `NOPASSWD: ALL`). Example intent:

- Allow `mitch` to run specific wrapper-invoked commands only (unittest, `configure-pi-paths.sh`, journalctl tails).
- **Reject** open `bash`, `apt`, `systemctl edit`, `deploy-all.sh` via sudo.

Get this wrong and Layer 2 becomes a root shell — review before apply.

**Explicitly rejected at this layer:**

- `deploy-all.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`
- `systemctl edit`, `apt`, `reboot`, `poweroff`
- Arbitrary `bash -c …`, **stdin payloads**, unknown tokens

**Verification (Phase 2 exit):**

```bash
ssh mpe-yolo@mpe-pi                    # forced command only; no PTY shell
ssh mpe-yolo@mpe-pi 'bash'             # rejected
echo 'id' | ssh mpe-yolo@mpe-pi        # rejected (stdin)
ssh mpe-yolo@mpe-pi 'deploy-all.sh'    # rejected (unknown token)
# No code path reachable as mpe-yolo may execute bash -s / heredoc stdin
```

---

## Layer 3 — `mpe-cli` on Racknerd (yolo profile)

Install [`mpe-cli`](https://github.com/MitchSchwartz/mpe-cli) on Racknerd.

### YOLO runtime identity

| Item | Value |
|---|---|
| Racknerd OS user | **`claude-sandbox`** (Claude Code YOLO runner) |
| Config path | `/home/claude-sandbox/.config/mpe/mpe-yolo.env` |
| Env var | **`MPE_CLI_CONFIG`** — the only wiring mechanism (`lib/config.sh`; no `--config` flag per mpe-cli AGENTS.md rule 2) |

**Config file:** root-owned **`mpe-yolo.env`** mode **0444** (agent cannot rewrite). Mitch installs/updates; YOLO sessions only read.

```bash
PI_HOST=mpe-pi
PI_USER=mpe-yolo
SSH_KEY=/home/claude-sandbox/.ssh/mpe_yolo_ed25519
```

**Session wiring:** export `MPE_CLI_CONFIG=~/.config/mpe/mpe-yolo.env` in `claude-yolo.sh` / bootstrap — never overwrite laptop `~/.config/mpe/mpe.env`.

### Phase 3 deliverable — token dispatch mode

Add a YOLO code path in `mpe-cli` (all subcommands used from Racknerd):

- Remote call = **`mpe_cli_ssh "<token>"`** (single argv string), **not** `mpe_cli_remote_bash`.
- Token vocabulary shared with `mpe-yolo-remote.sh` (document in both repos).
- Laptop profile may keep heredoc path for `mitch@pi` until a later unification — YOLO profile must not use it.

### Phase A allowlist (read + test)

Reference enums from **`mpe-cli`** (authoritative):

| Subcommand | Remote token (examples) |
|---|---|
| `mpe ping` | `ping` |
| `mpe status` | `status` |
| `mpe logs <unit> [-n N]` | `<unit>` ∈ **`surge`, `touch`, `looper`, `jackd`, `watchdog`**; `-n` capped at **200** (`MPE_LOG_LINES_MAX`) |
| `mpe diagnose` | `diagnose` |
| `mpe sysinfo` | `sysinfo` |
| `mpe osc-check` | `osc-check` |
| `mpe test pi <suite>` | `test-pi-<suite>`; `<suite>` ∈ **`all`, `apc`, `control-surfaces`, `looper`, `midi`, `touch`, `audio`, `surge`, `patch`, `calibration`, `system`** |

No free-form remote arguments. New suites require mpe-cli change + spec reference update.

**Exit contract on Racknerd:** propagate exit codes from Pi. Treat **exit 3** (partial suite — modules absent from checkout) as **failure** for YOLO smoke. Never pass **`--allow-partial`** from Racknerd.

### Phase B allowlist (optional — separate Gate A amendment)

| Subcommand | Gate | Notes |
|---|---|---|
| `mpe looper deploy <branch>` | **`pi_soak` cleared** (session gate file) | `<branch>` enum: **`dev`** or **`yolo/*`** only — add `mpe_cli_validate_yolo_branch` in mpe-cli (today `validate_git_branch` is charset-only, not pattern) |
| `git checkout` on Pi | **`pi_soak`** | Only via deploy token, not raw git |

**Phase B threat — test after deploy:** Agent can merge code to `yolo/*`, deploy to Pi, then run `mpe test pi` — unittest executes **as `mitch`**. Mitigations (pick before enabling Phase B):

1. Run Pi tests only against a **fixed ref** (e.g. `dev`) while deploy uses a separate worktree path, **or**
2. Require Mitch soak on branch before `pi_soak` clear includes deploy+test, **or**
3. Run tests as a dedicated unprivileged user (larger change).

Document chosen mitigation in DECISIONS.md before Phase 6.

**Still denied on Racknerd (all phases):**

- `mpe restart *`
- `deploy-all.sh`, profile scripts (direct or via SSH)
- Raw **`ssh`, `scp`, `rsync`** (any host)
- Edits to **`~/.config/mpe/`** (any file)
- Edits to `/etc/mpe/mpe.env` on Pi

Implement new capability as **mpe-cli subcommands** per appliance-cli pattern — not SSH allowlist widening.

---

## Layer 4 — YOLO guardrails (Racknerd)

The guard is **deny-by-default-then-allow** (`yolo-shell-guard.sh` ends in `allow`). Pi access requires **new deny rules**, not bolting an allowlist onto the final `allow`.

### Required guard changes

| Change | Rationale |
|---|---|
| Deny **all** raw `ssh`, `scp`, `rsync` | Replace `raspberrypi` hostname match — `ssh mitch@mpe-pi` must deny |
| Deny **any** `mpe` invocation not matching Phase A/B allowlist regex | Includes **`/path/to/mpe …`** path-prefix bypass |
| Deny writes to **`.config/mpe/`** | Config `source` under `set -a` is code execution + host retarget |
| Deny **`mpe restart`** (keep existing) | Mitch-only |
| Deny Phase B deploy tokens unless **session gate file** includes `pi_soak` | Per-command enforcement — not session-start-only |

### Session gate file (new — closes pi_soak gap)

`check-yolo-gates.sh` at session start writes e.g. `~/.cache/mpe-yolo/session-gates.json` (mode 600) with cleared gates for the **current task id**. `yolo-shell-guard.sh` reads it on each Bash hook:

- If command matches Phase B deploy allowlist and `pi_soak` ∉ cleared set → **deny**.
- File is task-scoped; new session / task re-runs gate check.

**Verification:** Phase 6 exit must prove **denial** with `pi_soak` uncleared, not only success when cleared.

### `scripts/yolo/check-pi-access-lockdown.sh`

Mirror `check-onecli-lockdown.sh` pattern. Include **negative** probes:

**Deny probes:**

- `ssh mitch@mpe-pi`, `scp x mitch@mpe-pi:`, `rsync x mitch@mpe-pi:`
- `deploy-all.sh`, `mpe restart surge`
- `/home/claude-sandbox/.local/bin/mpe restart surge` (path-prefix)
- `bash -c 'mpe restart surge'`
- `echo x > ~/.config/mpe/mpe-yolo.env`
- Phase B deploy command with session gate file **without** `pi_soak`

**Allow probes (Phase A enabled):**

- `MPE_CLI_CONFIG=~/.config/mpe/mpe-yolo.env mpe ping`
- `MPE_CLI_CONFIG=~/.config/mpe/mpe-yolo.env mpe test pi audio`

Wire into `check-guardrails.sh` and `bootstrap-nerdrack.sh`.

### Queue human gates (existing names — enforcement extended)

| Gate | Cleared by Mitch when |
|---|---|
| `pi_soak` | Pi deploy or branch checkout on appliance allowed |
| `systemd_change` | systemd unit changes in task |
| `audio_profile` | Profile / JACK / Surge audio changes |
| `mpe_env` | `/etc/mpe/mpe.env` changes |

Enqueue example:

```bash
bash scripts/yolo/enqueue-yolo-task.sh add ... --gate pi_soak
bash scripts/yolo/enqueue-yolo-task.sh clear-gate --id my-task pi_soak  # Mitch only, laptop
bash scripts/yolo/enqueue-yolo-task.sh approve --id my-task
```

---

## Mitch-only forever (even after this spec ships)

- `deploy-all.sh` without explicit future spec amendment
- `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`
- `mpe restart *` from Racknerd
- `sudo apt`, reboot, poweroff on Pi or Racknerd
- Ear tests B2/B10
- Promote appliance branch to `main`
- Edit Tailscale ACL, Pi `authorized_keys`, or `mpe-yolo-remote.sh` from YOLO sessions
- Edit root-owned `mpe-yolo.env` from YOLO sessions

---

## Phased rollout

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0 — Gate A** | This spec approved | Mitch marks **Status: Approved** — **Phase A scope only** (see below) |
| **1 — Tailscale** | Pi + Racknerd on tailnet; ACL deployed | TCP `:22` OK; other ports/LAN unreachable; `tailscale ping` supplementary |
| **2 — Pi SSH** | `mpe-yolo` user + `mpe-yolo-remote.sh` + narrow sudoers | No interactive shell; **stdin rejected**; audit log writes; no `bash -s` path |
| **3 — mpe-cli** | Racknerd install + root-owned yolo env + **token dispatch** | Manual: `MPE_CLI_CONFIG=… mpe test pi audio` from Racknerd reaches Pi; **no heredoc stdin** on wire |
| **4 — Guards** | shell-guard updates + session gate file + `check-pi-access-lockdown.sh` | `check-guardrails.sh` green on Racknerd; bypass probes pass |
| **5 — YOLO smoke** | Queued task: run Pi test suite only | YOLO log shows Pi test run; exit ≠ 3; no deploy |
| **6 — Deploy (opt.)** | `mpe looper deploy` + `pi_soak` session gate | Supervised deploy with gate cleared; **deny probe** with gate uncleared |

**Phase B (deploy + test-after-deploy mitigation)** requires a **separate Gate A amendment** after Phase A soak — do not bundle with initial approval.

**Rollback (complete):**

1. Remove Racknerd from tailnet / revert ACL
2. Remove `mpe-yolo` `authorized_keys` entry (or delete `mpe-yolo` user)
3. Remove `/usr/local/sbin/mpe-yolo-remote.sh` and sudoers drop-in
4. Revert `yolo-shell-guard.sh` + remove session gate file wiring
5. Remove root-owned `mpe-yolo.env` from Racknerd

YOLO returns to unit-test-only on VPS.

**Not in v1:** concurrent session locking (two tasks hitting one Pi), key rotation on Racknerd rebuild — document in DECISIONS when needed.

---

## Acceptance checklist (before Phase 5)

- [ ] Racknerd: `MPE_CLI_CONFIG=~/.config/mpe/mpe-yolo.env mpe ping` → OK  
- [ ] Racknerd: `mpe test pi audio` → reaches Pi; **exit 0 only if full suite ran** (exit 3 = fail)  
- [ ] Racknerd: `ssh mitch@mpe-pi` → **guard deny** (before SSH)  
- [ ] Racknerd: `ssh mpe-yolo@mpe-pi` → no shell; stdin rejected  
- [ ] Racknerd: LAN IP unreachable  
- [ ] YOLO: `deploy-all.sh` → guard deny  
- [ ] YOLO: `mpe restart surge` and `/path/mpe restart surge` → guard deny  
- [ ] YOLO: `racknerd onecli` / raw `ssh` / `scp` / `rsync` → guard deny  
- [ ] YOLO: write to `~/.config/mpe/` → guard deny  
- [ ] Laptop: Mitch admin path unchanged  

---

## Documentation updates (on implementation)

- [`docs/local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) — Pi row, Phase A/B table  
- [`AGENTS.md`](../AGENTS.md) — Racknerd Pi rules vs laptop  
- [`Documents/DECISIONS.md`](../Documents/DECISIONS.md) — dated approval row  
- `mpe-cli` — token dispatch, yolo profile, branch enum, AGENTS.md allowlist strings  
- OM-Repo `Docs/appliance-cli-pattern.md` — Racknerd yolo profile subsection  

---

## Gate A approval

**Approver:** Mitch  
**Date:** _pending_  
**Scope approved:** **Phase A only** _(Phase B requires separate amendment after Phase A soak and Phase B mitigation decision)_

When approved, change **Status** line at top to `Approved` and record date here.

**Do not approve Phase A+B together** until session gate enforcement (§Layer 4) and Phase B test-after-deploy mitigation (§Layer 3) are implemented and probed.
