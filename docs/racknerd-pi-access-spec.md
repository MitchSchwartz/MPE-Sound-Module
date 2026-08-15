# Racknerd YOLO → MPE Pi access (Tailscale + command containment)

**Status:** Draft — Gate A (Mitch approval) required before implementation  
**Created:** 2026-08-15 (America/Toronto)  
**Last updated:** 2026-08-15 17:31 (America/Toronto)

**Related:** [`local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) · [`AGENTS.md`](../AGENTS.md) · OM-Repo [`Docs/appliance-cli-pattern.md`](https://github.com/opsMachine/OM-Repo/blob/main/Docs/appliance-cli-pattern.md) · [`mpe-cli`](https://github.com/MitchSchwartz/mpe-cli)

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

## Non-goals

- Replacing Mitch's full admin path (`mitch@pi`, laptop `mpe` with restart/deploy).
- Subnet routing from Pi into `192.168.x.x` for the agent path.
- Automated ear tests (B2/B10) or promotion `dev` → `main` on the appliance.
- Running Surge/JACK **on** Racknerd (tests execute **on** the Pi).

---

## Threat model

| Threat | Mitigation layer |
|---|---|
| Compromised Racknerd scans home LAN | Tailscale ACL: Pi:22 only; **no** subnet routes |
| Compromised Racknerd pivots to other tailnet hosts | ACL deny racknerd-yolo → all except `mpe-pi:22` |
| Agent obtains interactive Pi shell | Forced-command SSH user; `no-pty` |
| Agent runs `deploy-all.sh`, audio profile, systemd | Guard deny + forced-command deny + queue gates |
| Agent runs raw `ssh`/`scp`/`rsync` | Guard deny; only `mpe` with fixed subcommands |
| YOLO `--dangerously-skip-permissions` | Narrow allowlist; no passthrough args to remote shell |

---

## Architecture

```text
┌──────────────── Racknerd VPS ─────────────────┐
│  claude-yolo.sh → yolo-shell-guard (allowlist)  │
│       → mpe-cli (yolo profile, ~/.config/…)     │
└───────────────────────┬─────────────────────────┘
                        │ Tailscale (ACL: :22 only)
                        ▼
┌──────────────── MPE Pi ─────────────────────────┐
│  sshd → mpe-yolo user → mpe-yolo-remote.sh     │
│       → fixed repo scripts as mitch (sudo)      │
└─────────────────────────────────────────────────┘

Mitch (laptop): mitch@pi — normal shell, separate key, unchanged.
```

---

## Layer 1 — Tailscale (network containment)

### Pi

- Join tailnet with stable name (e.g. `mpe-pi` or existing `raspberrypi2`).
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
# From Racknerd — must succeed
tailscale ping mpe-pi
mpe ping   # after Layer 3

# From Racknerd — must fail
ping 192.168.1.210
ssh mitch@mpe-pi          # wrong key / user
curl http://mediacenter:8096  # or any LAN/tailnet host except Pi SSH
```

---

## Layer 2 — Pi SSH (no agent shell)

### Two identities — separate keys

| Identity | OS user | Key location | Access |
|---|---|---|---|
| **Mitch (admin)** | `mitch` | Laptop | Full shell (LAN or tailnet) |
| **YOLO (agent)** | `mpe-yolo` | Racknerd only | Forced command only |

### Forced command

`/home/mpe-yolo/.ssh/authorized_keys` (example):

```text
command="/usr/local/sbin/mpe-yolo-remote.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,no-user-rc ssh-ed25519 AAAA... racknerd-yolo
```

### `mpe-yolo-remote.sh` (root-owned, 755, not writable by `mpe-yolo`)

**Contract:**

1. Accepts **one** invocation token from `mpe-cli` (e.g. `test-pi-audio`, `ping`, `status`, `logs-surge-50`).
2. Maps token → fixed local command (no user-supplied shell fragments).
3. Runs via `sudo -u mitch -H` only predefined scripts under `~/MPE-Module/scripts/…`.
4. Logs every invocation (timestamp, token, exit code) to `/var/log/mpe-yolo-remote.log`.
5. Rejects unknown tokens with exit 1.

**Explicitly rejected at this layer:**

- `deploy-all.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`
- `systemctl edit`, `apt`, `reboot`, `poweroff`
- Arbitrary `bash -c …` from remote

**Verification (Phase 2 exit):**

```bash
ssh mpe-yolo@mpe-pi                    # runs forced command or disconnects; no PTY shell
ssh mpe-yolo@mpe-pi 'bash'             # rejected
ssh mpe-yolo@mpe-pi 'deploy-all.sh'    # rejected
```

---

## Layer 3 — `mpe-cli` on Racknerd (yolo profile)

Install [`mpe-cli`](https://github.com/MitchSchwartz/mpe-cli) on Racknerd.

**Config:** `~/.config/mpe/mpe-yolo.env` (mode `600`, Racknerd only — not on laptop default path)

```bash
PI_HOST=mpe-pi                    # Tailscale name
PI_USER=mpe-yolo
SSH_KEY=/home/claude-sandbox/.ssh/mpe_yolo_ed25519
MPE_YOLO_REMOTE=1                 # dispatcher uses forced-command tokens only
```

**Environment wiring:** YOLO sessions set `MPE_CONFIG_FILE=~/.config/mpe/mpe-yolo.env` (or `mpe-cli` flag) — never overwrite Mitch's laptop `~/.config/mpe/mpe.env`.

### Phase A allowlist (read + test)

| Subcommand | Remote token / behavior |
|---|---|
| `mpe ping` | connectivity |
| `mpe status` | read-only status |
| `mpe logs surge\|touch\|watchdog [-n N]` | `-n` capped at 200 |
| `mpe diagnose` | read-only |
| `mpe sysinfo` | read-only |
| `mpe osc-check` | read-only |
| `mpe test pi <suite>` | `<suite>` enum: `audio`, `looper`, `apc`, `control-surfaces`, `all`, … (fixed list in mpe-cli) |

No free-form remote arguments. New suites require mpe-cli change + spec update.

### Phase B allowlist (optional — after Phase A soak)

| Subcommand | Gate | Notes |
|---|---|---|
| `mpe looper deploy <branch>` | **`pi_soak` cleared** | `<branch>` enum: `dev` or `yolo/*` pattern match |
| `git checkout` on Pi | **`pi_soak`** | Only via fixed deploy subcommand, not raw git |

**Still denied on Racknerd (all phases):**

- `mpe restart *`
- `deploy-all.sh`, profile scripts (direct or via SSH)
- Raw `ssh`, `scp`, `rsync`
- Edits to `~/.config/mpe/mpe-yolo.env`, `/etc/mpe/mpe.env`

Implement new capability as **mpe-cli subcommands** per appliance-cli pattern — not SSH allowlist widening.

---

## Layer 4 — YOLO guardrails (Racknerd)

Update `scripts/yolo/yolo-shell-guard.sh`:

| Current | Target |
|---|---|
| Block all Pi / `mpe` | Allow **exact** Phase A/B strings |
| Block `ssh … raspberrypi` | Keep blocking **raw** ssh/scp/rsync |
| Block `deploy-all.sh` | Keep unless task cleared `pi_soak` + Phase B enabled |

Add `scripts/yolo/check-pi-access-lockdown.sh`:

- Probe deny: raw `ssh mitch@…`, `deploy-all.sh`, `mpe restart`, `set-audio-profile.sh`
- Probe allow: `mpe ping`, `mpe test pi audio` (when Phase A enabled)

Wire into `check-guardrails.sh` and `bootstrap-nerdrack.sh`.

### Queue human gates (existing — use as-is)

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

---

## Phased rollout

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0 — Gate A** | This spec approved | Mitch marks **Status: Approved** below |
| **1 — Tailscale** | Pi + Racknerd on tailnet; ACL deployed | Ping Pi; LAN/other nodes unreachable from Racknerd |
| **2 — Pi SSH** | `mpe-yolo` user + `mpe-yolo-remote.sh` | No interactive shell; audit log writes |
| **3 — mpe-cli** | Racknerd install + yolo env + subcommands | Manual: `mpe test pi audio` from Racknerd |
| **4 — Guards** | shell-guard allowlist + `check-pi-access-lockdown.sh` | `check-guardrails.sh` green on Racknerd |
| **5 — YOLO smoke** | Queued task: run Pi test suite only | YOLO session log shows Pi test run; no deploy |
| **6 — Deploy (opt.)** | `mpe looper deploy` + `pi_soak` gate | One supervised deploy with gate cleared |

**Rollback:** remove Racknerd from tailnet; delete `mpe-yolo` authorized_keys; revert guard allowlist — YOLO returns to unit-test-only on VPS.

---

## Acceptance checklist (before Phase 5)

- [ ] Racknerd: `mpe ping` → OK  
- [ ] Racknerd: `mpe test pi audio` → reaches Pi (pass/fail of tests is separate)  
- [ ] Racknerd: `ssh mitch@mpe-pi` → denied  
- [ ] Racknerd: `ssh mpe-yolo@mpe-pi` → no shell  
- [ ] Racknerd: LAN IP unreachable  
- [ ] YOLO: `deploy-all.sh` → guard deny  
- [ ] YOLO: `mpe restart surge` → guard deny  
- [ ] YOLO: `racknerd onecli` / raw `ssh` → guard deny (existing lockdown)  
- [ ] Laptop: Mitch admin path unchanged  

---

## Documentation updates (on implementation)

- [`docs/local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) — Pi row, Phase A/B table  
- [`AGENTS.md`](../AGENTS.md) — Racknerd Pi rules vs laptop  
- [`Documents/DECISIONS.md`](../Documents/DECISIONS.md) — dated approval row  
- `mpe-cli` — `AGENTS.md` + allowlist for yolo profile  
- OM-Repo `Docs/appliance-cli-pattern.md` — Racknerd yolo profile subsection  

---

## Gate A approval

**Approver:** Mitch  
**Date:** _pending_  
**Scope approved:** Phase A only / Phase A+B _(circle one when approving)_

When approved, change **Status** line at top to `Approved` and record date here.
