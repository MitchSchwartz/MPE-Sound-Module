# Racknerd YOLO → MPE Pi access (Tailscale + command containment)

**Status:** Draft — Gate A (Mitch approval) required before implementation  
**Created:** 2026-08-15 (America/Toronto)  
**Last updated:** 2026-08-15 (America/Toronto) — rev 2

**Related:** [`local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) · [`AGENTS.md`](../AGENTS.md) · OM-Repo [`Docs/appliance-cli-pattern.md`](https://github.com/opsMachine/OM-Repo/blob/main/Docs/appliance-cli-pattern.md) · [`mpe-cli`](https://github.com/MitchSchwartz/mpe-cli)

**Review audit:** 2026-08-15 — spec revised against code (`yolo-shell-guard.sh`, `check-yolo-gates.sh`, `mpe-cli`). Do **not** approve until Layer 2 ↔ 3 seam (token dispatch) is reflected here and in Phase deliverables.

**Review audit 2:** 2026-08-15 (rev 2) — second pass against the same code closed four self-referential controls: the `MPE_CLI_CONFIG` pointer was agent-settable (making the 0444 config moot), the session gate file was agent-writable (making it a comment, not a gate), the stdin-rejection contract was not implementable, and the token grammar was undefined. See §Layer weight for the reframing those fixes rest on.

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

**Layer weight (important):** Layers **1–2** (Tailscale ACL, forced-command wrapper) are **containment** — they hold even if Racknerd is compromised. Layers **3–4** (`mpe-cli`, shell guard) are **ergonomics and mistake prevention** on the agent host. Do not treat four layers as equal strength.

**This is not a compromise scenario — it is the normal operating state.** The agent runs as `claude-sandbox` and that user *reads the `mpe-yolo` private key*, because `mpe-cli` needs it. `yolo-shell-guard.sh` is a `PreToolUse` hook: it inspects Bash **tool calls**, not `execve`. Any Python script, Makefile, or test module the agent writes can invoke `ssh -i mpe_yolo_ed25519 …` with the guard never consulted. No allowlist regex on Racknerd changes that.

Therefore:

> **The Pi token map in `mpe-yolo-remote.sh` is the complete definition of what the agent can do to the Pi.** Everything on Racknerd is mistake-prevention for a cooperative agent.

Two consequences that drive the rest of this spec:

1. **Gate A review effort belongs on the token map** (§Layer 2), not on guard regexes. A token that is too broad is a real hole; a missing guard regex is an annoyance.
2. **A control on Racknerd is only real if the agent user cannot forge its input.** Anything the agent can write — a config file it points at, a gate file it owns — is a comment, not a control. Both such holes are closed below (§Layer 3 config pinning, §Layer 4 session gate file); apply the same test to any control added later.

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
| Agent points `MPE_CLI_CONFIG` at a config **it** wrote (retarget host/key; `source` under `set -a` = code exec) | Guard requires the literal blessed path on every `mpe` call; `mpe-cli` rejects a non-root-owned config (§Layer 3) |
| Agent forges the session gate file to fake a cleared `pi_soak` | Gate file lives in a root-owned directory the agent cannot write (§Layer 4) |
| Agent calls `ssh -i <key>` from inside Python/Make, bypassing the Bash hook | **Not containable at L3/L4** — this is the normal state, not a compromise. Contained only by L2 token map |
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

**Tailscale ACLs are allow-only with implicit default-deny — there is no deny grant.** Write the one grant; the denials below are the *absence* of a grant, not rules to author. Reviewing this policy means checking that nothing else was added, not looking for deny lines.

| Requirement | How it is expressed | Intent |
|---|---|---|
| `tag:racknerd-yolo` → `tag:mpe-pi`:22 | The **only** grant with `tag:racknerd-yolo` as source | Agent path only |
| No other tailnet nodes reachable | Implicit default-deny — add no second grant | No lateral movement |
| No LAN pivot via Pi | Pi advertises no routes; `tag:racknerd-yolo` appears in no `autoApprovers` and in no grant naming a CIDR | No `192.168.x.x` from the VPS |
| Mitch laptop → `tag:mpe-pi`:22 | Existing grant, unchanged | Admin unchanged |

**Verification (manual, Phase 1 exit):**

```bash
# From Racknerd — connectivity (supplementary; not ACL proof alone)
tailscale ping mpe-pi

# From Racknerd — TCP ACL for agent path (primary)
nc -zv mpe-pi 22          # must succeed
```

**Negative port probe — do it properly.** `nc -zv mpe-pi <closed-port>` fails identically whether the ACL blocks it or nothing is listening, so it is *not* evidence. Give the port a listener first:

```bash
# On the Pi (temporary, for this probe only):
nc -l 8099
# From Racknerd — must FAIL despite a live listener (this is the ACL proof):
nc -zv mpe-pi 8099
```

```bash
# From Racknerd — no routes offered to this node (structural check, not a probe)
tailscale status --json | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["Self"].get("PrimaryRoutes"))'   # expect None
tailscale netcheck   # sanity only

# From Racknerd — LAN / other tailnet (must fail)
ping 192.168.1.210
curl http://mediacenter:8096

# Pi SSH auth (separate from ACL — wrong identity/key)
ssh mitch@mpe-pi          # must fail (wrong user/key for agent path)
# After Layer 3:
MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env mpe ping   # must succeed
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

**This wrapper is the whole security boundary** (see §Layer weight). Review the token map as if nothing on Racknerd existed — because for a determined agent, nothing on Racknerd does.

**Contract:**

1. Reads **one token** from `SSH_ORIGINAL_COMMAND` — **not** from stdin. The token must match the full string against a fixed table; no prefix matching, no splitting, no arguments.
2. **Discards stdin unconditionally** — first executable line is:

   ```bash
   exec </dev/null
   ```

   Do **not** try to detect whether stdin is empty. Under `no-pty` stdin is always a pipe; "is it empty" cannot be answered without a read that blocks when the client sends nothing and does not close. Closing the payload is both simpler and strictly safer than detecting it, and it makes the `bash -s` heredoc bypass structurally impossible rather than caught.
3. Maps token → fixed local command (no user-supplied shell fragments).
4. Runs via **`sudo -u mitch -H`** only for predefined operations:
   - **Read tokens:** fixed read-only scripts or systemd journal tails as documented in token map.
   - **`test-pi-<suite>` tokens:** run `python3 -m unittest …` in `~/MPE-Module` via a **fixed suite→module list** (same registry as `mpe-cli` — not agent-supplied paths).
   - **Phase B deploy token:** `configure-pi-paths.sh` + `git checkout` for **enum-validated branch only** (see Layer 3).
5. Logs every invocation (timestamp, token, exit code) to `/var/log/mpe-yolo-remote.log` (add logrotate; no unbounded growth).
6. Rejects unknown tokens with exit 1 — logging the **rejected token verbatim**, since that log line is the only tripwire for an agent probing the map.

### Token grammar — fixed strings, no arguments

The token table is a **closed set of literal strings**. A token carries no parameters, and the wrapper never parses one. This is the rule that keeps "no user-supplied shell fragments" true rather than aspirational.

| Token | Fixed command (intent) |
|---|---|
| `ping`, `status`, `sysinfo`, `diagnose`, `osc-check` | Corresponding read-only `mpe-cli` operation |
| `logs-surge`, `logs-touch`, `logs-looper`, `logs-jackd`, `logs-watchdog` | Journal tail for that unit, **fixed at `MPE_LOG_LINES_MAX` (200) lines** |
| `test-pi-<suite>` — one per name in `MPE_TEST_SUITE_NAMES` | Fixed suite→module list (§Layer 3) |
| Phase B only: `deploy-dev`, `deploy-yolo-<slug>` | See Phase B branch enum (§Layer 3) |

**`-n` does not exist in the YOLO profile.** Earlier drafts listed `logs-surge-50` as a token and separately allowed `-n` capped at 200, which implied either ~1000 literal tokens or an argument parser — the second reopening the injection surface the token map exists to close. The agent has no need for a tunable tail: it gets 200 lines or it asks Mitch. `mpe logs <unit> -n N` remains available on the **laptop** profile, where `mpe_cli_clamp_log_lines` handles it.

New tokens require a change in **both** `mpe-yolo-remote.sh` and `mpe-cli`, plus a spec revision. Adding a token is a security change, not a feature.

### sudoers (Mitch-only gate — specify before Phase 2)

Add a **narrow** sudoers drop-in (not `NOPASSWD: ALL`). Example intent:

- Allow `mitch` to run specific wrapper-invoked commands only (unittest, `configure-pi-paths.sh`, journalctl tails).
- **Reject** open `bash`, `apt`, `systemctl edit`, `deploy-all.sh` via sudo.

Get this wrong and Layer 2 becomes a root shell — review before apply.

### The Phase A invariant (write it down, it is load-bearing)

`test-pi-<suite>` runs `python3 -m unittest` **as `mitch`**, and a Python test module is arbitrary code. Phase A is safe for exactly one reason:

> **INVARIANT (Phase A): the Pi's checkout contains no agent-authored commit.** The ref on the appliance is Mitch-controlled; the agent can run those tests but cannot change what they are.

This is not a Phase B concern that arrives when deploy is enabled — it is the assumption Phase A already rests on, and it breaks *silently*. Anyone running `git pull` on the Pi for an unrelated reason, on a branch that happens to carry agent-authored test work, converts Phase A into Phase B without any gate firing.

Consequences:

- Phase A exit criteria must **assert** the invariant, not assume it (see Phase 5).
- Phase B's job is to replace this invariant with an enforced control, which is what the three mitigation options in §Layer 3 are choosing between. Option 3 (dedicated unprivileged test user) is the only one that removes the invariant rather than restating it, and is the recommended choice if Phase B is ever enabled.

**Explicitly rejected at this layer:**

- `deploy-all.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`
- `systemctl edit`, `apt`, `reboot`, `poweroff`
- Arbitrary `bash -c …`, **stdin payloads**, unknown tokens

**Verification (Phase 2 exit):**

```bash
ssh mpe-yolo@mpe-pi                    # forced command only; no PTY shell
ssh mpe-yolo@mpe-pi 'bash'             # rejected (unknown token)
ssh mpe-yolo@mpe-pi 'deploy-all.sh'    # rejected (unknown token)
ssh mpe-yolo@mpe-pi 'ping; id'         # rejected — full-string match, not prefix
ssh mpe-yolo@mpe-pi 'logs-surge -n 5000'  # rejected — tokens take no arguments

# stdin is discarded, not detected — these must run the token and ignore the payload,
# and must never hang:
echo 'id' | ssh mpe-yolo@mpe-pi ping           # runs ping; 'id' never executes
ssh mpe-yolo@mpe-pi 'bash -s' <<<'id'          # rejected (unknown token 'bash -s')
ssh mpe-yolo@mpe-pi ping < /dev/null           # runs ping, returns promptly

# Audit: every line above appears in /var/log/mpe-yolo-remote.log with its exit code,
# and each rejection logs the offending token verbatim.
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

**Session wiring:** export `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env` in `claude-yolo.sh` / bootstrap — never overwrite laptop `~/.config/mpe/mpe.env`.

#### Pin the pointer, not just the file (required)

A read-only config is worthless while the *pointer to it* is an ordinary environment variable. In [`lib/config.sh`](https://github.com/MitchSchwartz/mpe-cli/blob/main/lib/config.sh):

```bash
MPE_CLI_CONFIG="${MPE_CLI_CONFIG:-${HOME}/.config/mpe/mpe.env}"
```

and the file is sourced under `set -a` — so `source` is code execution, and any writable path the agent chooses retargets `PI_HOST` / `PI_USER` / `SSH_KEY` wholesale:

```bash
printf 'PI_HOST=evil\nPI_USER=root\nSSH_KEY=/tmp/k\n' > /tmp/x.env
MPE_CLI_CONFIG=/tmp/x.env mpe ping     # 0444 blessed config never consulted
```

Two independent fixes; **implement both** (belt and braces, and neither is sufficient alone — the guard misses non-Bash callers, the CLI check misses nothing but lives on a host the agent can edit):

1. **`mpe-cli` (Phase 3):** `mpe_cli_load_config` refuses a config that is not **root-owned and non-group/other-writable**, exiting non-zero with a clear message. This is correct behavior for the laptop too — it costs nothing there and closes the retarget path generally.
2. **`yolo-shell-guard.sh` (Phase 4):** every allowlisted `mpe` invocation must carry the **literal** string `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env`. Any `mpe` call with a different `MPE_CLI_CONFIG=`, or with none, is **denied**.

Note the existing "deny writes under `.config/mpe/`" rule does **not** cover this — the attack writes somewhere else entirely.

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
| `mpe logs <unit>` | `logs-<unit>`; `<unit>` ∈ **`surge`, `touch`, `looper`, `jackd`, `watchdog`**. Fixed at 200 lines — **`-n` is not accepted in the YOLO profile** (§Token grammar) |
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
3. **(Recommended)** Run tests as a dedicated unprivileged user (larger change).

Option 3 is recommended because options 1 and 2 restate the Phase A invariant (§Layer 2) as procedure, while 3 **removes** it: agent-authored Python then executes as a user with no access to `mitch`'s home, keys, or sudoers. Options 1 and 2 leave "arbitrary code as `mitch`" one operator mistake away, which is the same failure mode Phase A already carries and Phase B multiplies.

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

The guard is **allow-by-default** — `yolo-shell-guard.sh` runs a list of deny rules and ends in `allow`. Pi access requires **new deny rules**, not bolting an allowlist onto the final `allow`.

**Scope reminder:** this layer stops a cooperative agent from making a mistake. It does not stop `ssh -i <key>` called from inside a Python script, because the hook sees Bash tool calls only (§Layer weight). Budget review time accordingly.

### Required guard changes

| Change | Rationale |
|---|---|
| Deny **all** raw `ssh`, `scp`, `rsync` (any host) | Replace the current `raspberrypi` hostname match — today `ssh mitch@mpe-pi` **allows** |
| Deny **any** `mpe` invocation not matching Phase A/B allowlist regex | Includes **`/path/to/mpe …`** path-prefix bypass — today `(^|[;&|[:space:]])mpe[[:space:]]+restart` does not match `/home/claude-sandbox/.local/bin/mpe restart` |
| Deny any `mpe` call whose `MPE_CLI_CONFIG=` is absent or is not the blessed literal path | Closes the config-pointer retarget (§Layer 3) |
| Deny writes to **`.config/mpe/`** | Config `source` under `set -a` is code execution + host retarget |
| Deny **`--allow-partial`** on any `mpe test` invocation | Spec forbids it on Racknerd; nothing enforced it before |
| Deny **`mpe restart`** (keep existing, fix path-prefix) | Mitch-only |
| Deny Phase B deploy tokens unless **session gate file** shows `pi_soak` cleared | Per-command enforcement — not session-start-only |

### Session gate file (new — closes pi_soak gap)

`check-yolo-gates.sh` validates gates once at session start and **exits leaving no artifact**, so nothing at Bash-hook time can know whether `pi_soak` was cleared. The gate file closes that.

**It must not be forgeable by the agent.** A file at `~/.cache/mpe-yolo/session-gates.json` mode 600 is *owned by `claude-sandbox`* — the agent writes its own permission slip and the gate becomes a comment. Required instead:

| Property | Requirement |
|---|---|
| Location | **`/var/lib/mpe-yolo/session-gates.json`** — directory root-owned, mode **0755**, file **0644** |
| Writer | `check-yolo-gates.sh` via a small root-owned setuid-free helper invoked through a **narrow sudoers entry** (`claude-sandbox` may run *that one script*, nothing else) |
| Reader | `yolo-shell-guard.sh`, on every Bash hook |
| Content | Cleared gate names for the **current `YOLO_TASK_ID`**, plus the task id itself |
| Staleness | Guard denies if the file's task id ≠ current `YOLO_TASK_ID`, or if the file is missing |

The writer helper takes **no arguments from the agent**: it re-reads `yolo-queue.json` and derives cleared gates itself, exactly as `check-yolo-gates.sh` does today. The agent can trigger a refresh; it cannot dictate the contents.

If the sudoers helper is judged not worth it for v1, the honest alternative is to **not ship Phase B at all** — an agent-writable gate file is worse than no gate, because it reads as enforcement in every subsequent review.

**Verification:** Phase 6 exit must prove **denial** with `pi_soak` uncleared, and must prove the agent cannot flip it: `echo '{"pi_soak":true}' > /var/lib/mpe-yolo/session-gates.json` must fail on permissions.

### `scripts/yolo/check-pi-access-lockdown.sh`

Mirror `check-onecli-lockdown.sh` pattern. Include **negative** probes:

**Deny probes:**

- `ssh mitch@mpe-pi`, `scp x mitch@mpe-pi:`, `rsync x mitch@mpe-pi:`
- `ssh mpe-yolo@mpe-pi ping` — raw ssh denied even on the *sanctioned* path; `mpe-cli` is the only entrypoint
- `deploy-all.sh`, `mpe restart surge`
- `/home/claude-sandbox/.local/bin/mpe restart surge` (path-prefix)
- `bash -c 'mpe restart surge'`
- `echo x > ~/.config/mpe/mpe-yolo.env`
- `MPE_CLI_CONFIG=/tmp/x.env mpe ping` — **config-pointer retarget** (§Layer 3)
- `mpe ping` — bare, no `MPE_CLI_CONFIG` at all
- `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env mpe test pi audio --allow-partial`
- `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env mpe logs surge -n 5000` — `-n` not in YOLO profile
- Phase B deploy command with session gate file **without** `pi_soak`
- `echo '{"pi_soak":true}' > /var/lib/mpe-yolo/session-gates.json` — gate-file forgery

**Allow probes (Phase A enabled):**

- `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env mpe ping`
- `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env mpe test pi audio`
- `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env mpe logs surge`

**Known-blind-spot probe (documents the limit; asserts `allow`, does not fix it):**

- `python3 -c "import subprocess; subprocess.run(['ssh','-i','/home/claude-sandbox/.ssh/mpe_yolo_ed25519','mpe-yolo@mpe-pi','ping'])"`

  This **passes the guard**, by design and unavoidably — the hook does not see `execve`. Probe it as an `allow` with a comment naming §Layer weight, so the next reader learns the boundary from the harness instead of rediscovering it. Containment for this path is the Pi token map alone.

Wire into `check-guardrails.sh` and `bootstrap-nerdrack.sh`, reusing the `probe_deny` / `probe_allow` harness from `check-onecli-lockdown.sh`.

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
bash scripts/yolo/enqueue-yolo-task.sh clear-gate --id my-task --gate pi_soak  # Mitch only, laptop
bash scripts/yolo/enqueue-yolo-task.sh approve --id my-task
```

`clear-gate` requires **`--gate NAME`** — the gate name is not positional (`enqueue-yolo-task.sh` errors out otherwise). The guard separately denies `enqueue-yolo-task.sh (approve|clear-gate)` on Racknerd, so these run on the laptop only.

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
| **2 — Pi SSH** | `mpe-yolo` user + `mpe-yolo-remote.sh` (fixed token table, `exec </dev/null`) + narrow sudoers | No interactive shell; **stdin discarded, never hangs**; full-string token match; audit log writes incl. rejected tokens; no `bash -s` path |
| **3 — mpe-cli** | Racknerd install + root-owned yolo env + **token dispatch** + **root-owned-config check** | Manual: `MPE_CLI_CONFIG=… mpe test pi audio` from Racknerd reaches Pi; **no heredoc stdin** on wire; `MPE_CLI_CONFIG=/tmp/x.env mpe ping` refused by the CLI itself |
| **4 — Guards** | shell-guard updates + **root-owned** session gate file + `check-pi-access-lockdown.sh` | `check-guardrails.sh` green on Racknerd; all deny probes deny; gate-file forgery fails on permissions |
| **5 — YOLO smoke** | Queued task: run Pi test suite only | YOLO log shows Pi test run; exit ≠ 3; no deploy; **Phase A invariant asserted** — Pi checkout ref is Mitch-controlled and carries no agent-authored commit |
| **6 — Deploy (opt.)** | `mpe looper deploy` + `pi_soak` session gate | Supervised deploy with gate cleared; **deny probe** with gate uncleared |

**Phase B (deploy + test-after-deploy mitigation)** requires a **separate Gate A amendment** after Phase A soak — do not bundle with initial approval.

**Rollback (complete):**

1. Remove Racknerd from tailnet / revert ACL
2. Remove `mpe-yolo` `authorized_keys` entry (or delete `mpe-yolo` user)
3. Remove `/usr/local/sbin/mpe-yolo-remote.sh` and sudoers drop-in
4. Revert `yolo-shell-guard.sh` + remove session gate file wiring
5. Remove root-owned `mpe-yolo.env` from Racknerd

YOLO returns to unit-test-only on VPS.

**Not in v1:**

- **Concurrent session locking** (two tasks hitting one Pi). v1 therefore carries an **unenforced invariant: one YOLO session at a time**. This matters most for Phase B, where deploy-then-test is a non-atomic sequence on shared hardware — a second session can deploy between the two steps. Enforce with a lock on the Pi before Phase B ships.
- **Key rotation on Racknerd rebuild** — the rollback procedure covers revocation; rotation is manual until documented in DECISIONS.

---

## Acceptance checklist (before Phase 5)

This list is the canonical gate; the per-phase exit criteria above are its working breakdown. If the two ever disagree, fix the phase table.

**Containment (L1–L2) — these must hold on their own**

- [ ] Racknerd: TCP 22 to `mpe-pi` OK; **live listener on another port unreachable** (real ACL proof, not a closed-port false green)  
- [ ] Racknerd: LAN IP unreachable; `tailscale status` shows no routes offered to this node  
- [ ] Pi: `ssh mpe-yolo@mpe-pi` → no shell; unknown tokens rejected and logged verbatim  
- [ ] Pi: `echo 'id' | ssh mpe-yolo@mpe-pi ping` → runs `ping`, ignores payload, **returns promptly**  
- [ ] Pi: `ssh mpe-yolo@mpe-pi 'ping; id'` and `'logs-surge -n 5000'` → rejected (full-string match, no args)  
- [ ] Pi: sudoers drop-in reviewed line by line — no open `bash`, `apt`, `systemctl`, `deploy-all.sh`  
- [ ] Pi: **token table reviewed as the sole boundary** (§Layer weight) — signed off by Mitch  
- [ ] Pi: `/var/log/mpe-yolo-remote.log` written, logrotate in place  

**Phase A invariant**

- [ ] Pi checkout ref is Mitch-controlled and carries **no agent-authored commit** (§Layer 2)  

**Mistake-prevention (L3–L4) — real, but not containment**

- [ ] Racknerd: `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env mpe ping` → OK  
- [ ] Racknerd: `mpe test pi audio` → reaches Pi; **exit 0 only if full suite ran** (exit 3 = fail)  
- [ ] Racknerd: `MPE_CLI_CONFIG=/tmp/x.env mpe ping` → **denied by guard *and* refused by `mpe-cli`** (both paths)  
- [ ] Racknerd: bare `mpe ping` with no `MPE_CLI_CONFIG` → guard deny  
- [ ] Racknerd: `mpe test pi audio --allow-partial` → guard deny  
- [ ] Racknerd: `ssh mitch@mpe-pi` **and** `ssh mpe-yolo@mpe-pi ping` → guard deny (before SSH)  
- [ ] YOLO: `deploy-all.sh` → guard deny  
- [ ] YOLO: `mpe restart surge` and `/path/mpe restart surge` → guard deny  
- [ ] YOLO: `racknerd onecli` / raw `ssh` / `scp` / `rsync` → guard deny  
- [ ] YOLO: write to `~/.config/mpe/` → guard deny  
- [ ] YOLO: write to `/var/lib/mpe-yolo/session-gates.json` → **fails on permissions**  
- [ ] Harness documents the `ssh`-from-Python blind spot as a passing `allow` probe  

**Unchanged**

- [ ] Laptop: Mitch admin path unchanged (`mitch@pi`, `mpe restart`, `-n` on logs, heredoc path)  

---

## Documentation updates (on implementation)

- [`docs/local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) — Pi row, Phase A/B table  
- [`AGENTS.md`](../AGENTS.md) — Racknerd Pi rules vs laptop  
- [`Documents/DECISIONS.md`](../Documents/DECISIONS.md) — dated approval row  
- `mpe-cli` — token dispatch, yolo profile, branch enum, **root-owned-config check in `lib/config.sh`**, AGENTS.md allowlist strings  
- OM-Repo `Docs/appliance-cli-pattern.md` — Racknerd yolo profile subsection  

---

## Gate A approval

**Approver:** Mitch  
**Date:** _pending_  
**Scope approved:** **Phase A only** _(Phase B requires separate amendment after Phase A soak and Phase B mitigation decision)_

When approved, change **Status** line at top to `Approved` and record date here.

**Do not approve Phase A+B together** until session gate enforcement (§Layer 4) and Phase B test-after-deploy mitigation (§Layer 3) are implemented and probed.

**What Gate A is actually approving.** Not four layers of defense — two. Approving this means accepting:

1. The **Pi token table** is correct and minimal, because it is the only thing standing between an agent with a working SSH key and the appliance (§Layer weight).
2. The **Phase A invariant** holds and is checked: the Pi's checkout carries no agent-authored commit, because `test-pi-*` runs Python as `mitch` (§Layer 2).
3. Racknerd's guard and CLI are **mistake-prevention**, valuable but bypassable by any non-Bash caller — and the probe harness says so out loud rather than implying otherwise.

If any of those three reads as uncomfortable, that discomfort is the accurate one; the earlier four-equal-layers framing was not.
