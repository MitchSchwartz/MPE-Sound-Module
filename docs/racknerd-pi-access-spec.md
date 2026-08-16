# Racknerd YOLO → MPE Pi access (Tailscale + command containment)

**Status:** Draft — Gate A (Mitch approval) required before implementation  
**Created:** 2026-08-15 (America/Toronto)  
**Last updated:** 2026-08-16 (America/Toronto) — rev 5 (**scope change** — see ⚠️ Revision 5)

**Related:** [`local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) · [`AGENTS.md`](../AGENTS.md) · OM-Repo [`Docs/appliance-cli-pattern.md`](https://github.com/opsMachine/OM-Repo/blob/main/Docs/appliance-cli-pattern.md) · [`mpe-cli`](https://github.com/MitchSchwartz/mpe-cli)

**Review audit:** 2026-08-15 — spec revised against code (`yolo-shell-guard.sh`, `check-yolo-gates.sh`, `mpe-cli`). Do **not** approve until Layer 2 ↔ 3 seam (token dispatch) is reflected here and in Phase deliverables.

**Review audit 2:** 2026-08-15 (rev 2) — second pass against the same code closed four self-referential controls: the `MPE_CLI_CONFIG` pointer was agent-settable (making the 0444 config moot), the session gate file was agent-writable (making it a comment, not a gate), the stdin-rejection contract was not implementable, and the token grammar was undefined. See §Layer weight for the reframing those fixes rest on.

**Review audit 3:** 2026-08-15 (rev 3) — third pass **executed** the guard and `mpe-cli` against real payloads rather than reading them. Findings, in order of weight:

1. **`PI_MPE_MODULE` is a live remote command injection** in `mpe-cli` today, on the laptop profile, independent of this spec (§Layer 3 → *Config loading is the vulnerability*). **Fix it before Gate A; it is not gated on this spec.**
2. **Layer 4 is agent-editable, not merely agent-bypassable.** The guard script is a git-tracked file in the repo the agent is employed to edit, and `check-yolo-gates.sh`'s own nerdrack detector keys off an agent-writable marker. Rev 2's "four layers are really two" understated it (§Layer weight).
3. Rev 2's root-owned-config check as written **breaks the laptop**, contradicting this spec's own "laptop unchanged" requirement.
4. Rev 2's session gate file was unforgeable but derived from an **agent-writable queue** — root ownership moved the forgery one hop upstream.
5. Minor: `MPE_SURGE_LOG` is *not* an injection vector (it expands on the Pi); rev 2's `logs-surge` token description was wrong; the Phase 4/Phase 6 gate-proof assignment contradicted itself.

**Review audit 4:** 2026-08-16 (rev 4) — fourth pass **executed the guard against every claimed bypass** and read `mpe-cli`'s installer rather than assuming a normal binary install. Rev 3's findings all held under execution. What it missed:

1. **The `env -i` wrapper hands control to agent-writable code, including the file that enforces its own pin.** `install.sh` does `ln -sf $REPO_ROOT/bin/mpe ~/.local/bin/mpe`, and `bin/mpe` resolves `MPE_CLI_ROOT` via `readlink -f "$0"` and sources `lib/config.sh` from the **clone**. So `MPE_CLI_REQUIRE_ROOT_CONFIG` is enforced by a file `claude-sandbox` can edit (§Layer 3 → *Install provenance*). Rev 3 applied its own "can the agent edit the implementation" test to the guard and not to the binary its sanctioned entrypoint execs.
2. **No version pinning**, though `mpe-cli` ships `version --check X.Y.Z` built for exactly this and `bin/mpe` carries a version comment saying so. Combined with (1): the `PI_MPE_MODULE` fix gets ticked off on the laptop and can be silently absent — or reverted — on Racknerd, with every probe still green (§Layer 3, Phase 3).
3. **`install.sh` contradicts this spec's config model** — it seeds `~/.config/mpe/mpe.env` mode 600 owned by the invoking user, at the exact default path `lib/config.sh` falls back to. Running the documented installer as `claude-sandbox` produces an agent-owned config (§Layer 3).
4. **The guard emits no verdict when it breaks.** Executed: malformed hook input → Python traceback, **exit 1, no JSON**. Not agent-triggerable today (Claude Code builds the payload), but rev 3 adds a per-Bash-hook read of the session gate file under `set -euo pipefail` — which turns "deny if the file is missing" into "die, emit nothing, and let the call through" (§Layer 4 → *Fail closed*).
5. **`agentjail` appears nowhere in this spec** despite being the **second** `PreToolUse` Bash hook and a hard requirement of `check-guardrails.sh`. It resolves its binary from agent-settable env at an agent-owned default path and prints `allow` explicitly when absent — another half-nothing, but it belongs in the inventory (§Threat model).
6. **The ownership fix stopped one level too low.** Rev 3 root-owns the guard, hook config, queue, and marker. `claude-yolo.sh` — in-repo, agent-writable — is what *invokes* every gate script. Four deleted lines removes the whole gate system at once (§Layer 4 → *Gate state*).
7. Correction to rev 3's own severity discipline: the incomplete rollback list is documentation debt on a supervised teardown, **not** a 🔴. Fixed below and rated accordingly.

**Still unreadable by any reviewer:** `mpe-yolo-remote.sh`, its test suite, the sudoers drop-in, and the ACL do not exist yet. Rev 3 is right that the token map is the entire boundary — which means **every review so far has validated the analysis, not the boundary.**

---

## ⚠️ Revision 5 — scope change (2026-08-16). Read this before anything below.

**The goal stated in this document is not the goal.** Revisions 1–4 optimised for *keeping agent-authored code off the appliance*. The actual requirement is the opposite:

> **The agent deploys its own code to the Pi and tests it there — the same thing Mitch's laptop agents already do via `mpe-cli` today.**

That single sentence voids a large part of this spec. Consequences, in order:

1. **The Phase A invariant is dead.** "The Pi's checkout contains no agent-authored commit" is not a safety property to preserve; it is directly contrary to the goal. Every control derived from it — ref pinning, the pinned test worktree, the Phase A/B split itself — is deleted. See §The Phase A invariant, retained only as a record of why it cannot work.
2. **Arbitrary agent code executing on the Pi is a design input, not a threat.** Containment therefore cannot work by controlling *what code arrives*. It must work *below* the code: which user runs it, and what that user can reach.
3. **Phase B is the product; Phase A was never the deliverable.** The phased rollout below is retained for build order only. Do not read it as a scope boundary.
4. **This capability already exists on the laptop.** Local agents call `mpe looper deploy` and `mpe test pi` through the CLI, so agent-authored Python already runs on the appliance as `mitch`. Racknerd adds exactly three things: a **network path** (Layer 1 — genuinely new), a **host Mitch does not physically control**, and **no human in the loop** (YOLO runs `--dangerously-skip-permissions` unattended). That third one is an oversight multiplier, not a capability grant — and it is the honest reason for care.
5. **The load-bearing control is now the unprivileged run-as user** (§Layer 2 → *`mpe-agent`*), formerly filed as "Phase B mitigation option 3." It is the only design that contains agent code rather than restating an invariant — and it reduces exposure the **laptop** agents already carry, which makes it the highest-return item in this document.
6. **New requirement: a performance-mode interlock** (§Layer 2). One Pi, one sound card. The `flock` stops agent-vs-agent collision; nothing stopped an agent starting a test suite while Mitch is playing.

**What survives rev 5 unchanged:** the Tailscale ACL (Layer 1) as the only unconditional containment; the forced-command token map (Layer 2) as the reason a VPS never holds a general shell; the §Layer weight analysis; and the conclusion that Layers 3–4 are mistake-prevention the agent can edit.

**Status of the rev 3/4 🔴 items:**

| Item | State |
|---|---|
| `PI_MPE_MODULE` injection | ✅ **Fixed and shipped** — `mpe-cli` 1.3.0, `fix/pi-mpe-module-injection`, with `tests/test_config_injection.sh` verified to fail when reverted |
| Missing-config fallthrough | ✅ **Fixed and shipped** — same commit; hard error in `mpe_cli_require_config` |
| Pi GitHub credential | ✅ **Removed** — see §Layer 2 *Credentials on the appliance* |
| Root-owned `mpe-cli` install on Racknerd | Open — required before the 1.3.0 fix means anything there |
| Guard fail-closed, Layer 4 ownership | **Descoped** — see §Layer weight; not worth building given (4) above |

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

**Layer weight (important): this is not four layers. It is one and a half.**

| Layer | What it actually is |
|---|---|
| **1 — Tailscale ACL** | **Containment.** Holds regardless of what runs on Racknerd. |
| **2 — Pi forced command + token map** | **Containment** — *and today it is untested* (§Layer 2 test harness). Half a layer until it has one. |
| **3 — `mpe-cli`** | Mistake-prevention. Lives on a host the agent controls. |
| **4 — `yolo-shell-guard.sh`** | Mistake-prevention, **and the agent can edit the guard itself** (below). |

**None of this is a compromise scenario — it is the normal operating state.** Three independent reasons, each verified by execution:

1. The agent runs as `claude-sandbox` and that user *reads the `mpe-yolo` private key*, because `mpe-cli` needs it.
2. `yolo-shell-guard.sh` is a `PreToolUse` hook: it inspects Bash **tool calls**, not `execve`. Any Python script, Makefile, or test module the agent writes can invoke `ssh -i mpe_yolo_ed25519 …` with the guard never consulted.
3. **The guard is a git-tracked file in the repo the agent is employed to edit.** `sed -i s/x/y/ scripts/yolo/yolo-shell-guard.sh` returns `allow`. `claude-yolo.sh` launches with `--setting-sources project,local`, so the hook wiring in `.claude/settings.local.json` — gitignored, inside `$ROOT`, agent-writable — points at that editable script. Rev 2's threat table filed "Racknerd rewrites guard" under *compromise*. It is not a compromise; it is Tuesday.

Therefore:

> **The Pi token map in `mpe-yolo-remote.sh` is the complete definition of what the agent can do to the Pi.** Everything on Racknerd is mistake-prevention for a cooperative agent — and it is mistake-prevention the agent can delete.

Three consequences that drive the rest of this spec:

1. **Gate A review effort belongs on the token map** (§Layer 2), not on guard regexes. A token that is too broad is a real hole; a missing guard regex is an annoyance. Regex *precision* on Racknerd is close to irrelevant given point 3 above — what matters about the guard is **who owns the file**, not what it matches.
2. **A control on Racknerd is only real if the agent user cannot forge its input *or edit its implementation*.** Rev 2 applied only the first half of that test. Rev 3 applies both: anything the agent can write — a config it points at, a gate file it owns, a queue the gate reads, the guard script, the hook config, or the marker file that decides whether gates run at all — is a comment, not a control.
3. **The one control that is real is the least tested.** The Pi wrapper is asserted to be the whole boundary and is verified only by manual `ssh` commands a human types once. That inversion is closed in Phase 2 (§Layer 2 test harness) and is a Gate A condition.

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
| **Agent edits `yolo-shell-guard.sh` in the normal course of work** (git-tracked, in-repo; `sed -i` on it returns `allow`) | **Not containable while the guard lives in the repo.** Mitigated by moving guard + hook config to root-owned paths outside `$ROOT` (§Layer 4 guard ownership); until then, L4 is advisory |
| **Agent deletes `.claude/settings.local.json` so `is_nerdrack()` returns false** — `check-yolo-gates.sh` then exits 0 with "laptop mode: skipped" and *no gate is checked at all* | Root-owned nerdrack marker outside `$ROOT` (§Layer 4 gate state) |
| **Agent edits `.claude/primitives/yolo-queue.json` or writes `Status: Approved` into this spec file** — both are `mitch`-owned working-tree files that `check-yolo-gates.sh` reads; Gate A self-clears | Queue + approval state moved outside the agent-writable tree (§Layer 4 gate state) |
| **Agent sets `PI_MPE_MODULE` and gets arbitrary command execution on the Pi as `mitch`** — live today, laptop profile included | Quote + validate in `mpe-cli`; **fix independent of this spec** (§Layer 3) |
| Compromised Racknerd rewrites mpe-cli | **Not containable** at L3/L4 — rely on L1/L2 |
| **Agent edits the `mpe-cli` clone that `~/.local/bin/mpe` symlinks into** — including `lib/config.sh`, which enforces `MPE_CLI_REQUIRE_ROOT_CONFIG`. Normal-state, not compromise | Install root-owned outside `$HOME`; pin version (§Layer 3 *Install provenance*) |
| **Agent reverts a shipped `mpe-cli` fix on Racknerd and no check notices** — acceptance ticks the fix off on the laptop | `mpe version --check` in `bootstrap-nerdrack.sh` + root-owned install |
| **`agentjail` hook fails open** — `AGENTJAIL_HOOK_BIN` is agent-settable env defaulting to agent-owned `~/.agentjail/bin/`; wrapper prints `allow` if the binary is missing | None. Listed for inventory completeness — it is not a layer and must not be counted as one |
| **Guard crashes and emits no verdict** — exit 1 with no JSON is a non-blocking hook error, so the Bash call proceeds | Fail-closed trap + explicit deny branch on gate-file read (§Layer 4 *Fail closed*) |
| **Agent edits `claude-yolo.sh`** and removes the four lines that invoke every gate script | Launcher + gate scripts root-owned (§Layer 4 *Gate state*) |
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
- **Do NOT set `--shields-up` on the Pi** (rev 5 correction). Rev 1–4 listed it here as "optional if Pi-initiated connections are not needed." That is backwards: `--shields-up` blocks **incoming** tailnet connections while still permitting outbound. On the Pi it would block `tag:racknerd-yolo → mpe-pi:22` — G1, the entire point of this design — and cut Mitch's own tailnet admin path. It belongs on **Racknerd**, where nothing needs to initiate a connection *to* the VPS, and where it is free hardening (§Racknerd below).
- **Performance impact — measure, do not assume** (rev 5). Measured on the running appliance 2026-08-16: `jackd -R -P70 -s -d alsa -P hw:1 -r 48000 -p 256 -n 3` — **256 × 3 @ 48 kHz ≈ 5.3 ms per period**, jackd `SCHED_FIFO 70`, Surge `SCHED_FIFO 65`, load ~1.5 on 4 cores. Note `docs/LATENCY-SPIKE.md` still headlines **512** × 3; the running system is at **256**, so that document is stale and half the headroom it implies is not there.
- An idle `tailscaled` (DERP keepalives, periodic netmap polls, sub-1% CPU) is still very unlikely to fill 5.3 ms. The mechanism that *could* matter is network RX softirq jitter on the audio core — `eth0` holds IRQs 28/29 — and it only appears under real traffic, i.e. when the agent is working. Mitigation: systemd `CPUAffinity=` drop-in pinning `tailscaled` off the audio core; check NIC IRQ affinity if xruns appear.
- **Baseline caveat:** `mpe jack status` currently reports `xruns: 0 (since buffer -> 512)` while running at 256. That counter's reference point predates the current configuration, so it is **not** a valid pre-Tailscale baseline. Reset or restart the counter and record a fresh number under real playback before installing, or the before/after comparison cannot distinguish "no regression" from "no measurement."

### Racknerd

- Tailscale installed; node tagged **`tag:racknerd-yolo`** (dedicated identity).
- Not shared with Mitch's personal tailnet login where avoidable.
- **Set `tailscale set --shields-up=true` here** (rev 5). Nothing needs to open a connection to the VPS; this is the node where the option is correct and free.
- Disable key expiry for the tagged node, or the agent path dies silently in ~6 months and the failure will present as an ACL problem.

**Install notes (measured 2026-08-16 on Ubuntu 24.04 `noble`; Tailscale 1.102.2).** Two deviations from Tailscale's published apt instructions — both fail confusingly:

1. `https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-list` **404s.** The file that exists is **`noble.list`**. (`noble.noarmor.gpg` is correct and returns 200.)
2. `noble.list` ships **without a `signed-by=` attribute**, so `apt-get update` fails with `NO_PUBKEY 458CA832957F5868` and `Unable to locate package tailscale`. Write the sources line yourself:

```bash
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
  -o /usr/share/keyrings/tailscale-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu noble main" \
  > /etc/apt/sources.list.d/tailscale.list
apt-get update && apt-get install -y tailscale
```

The Pi is Debian, not Ubuntu — use the `raspbian`/`debian` path for its codename and expect the same two issues. Fold this into `bootstrap-nerdrack.sh`.

### ACL policy (requirements)

**Tailscale ACLs are allow-only with implicit default-deny — there is no deny grant.** Write the one grant; the denials below are the *absence* of a grant, not rules to author. Reviewing this policy means checking that nothing else was added, not looking for deny lines.

| Requirement | How it is expressed | Intent |
|---|---|---|
| `tag:racknerd-yolo` → `tag:mpe-pi`:22 | The **only** grant with `tag:racknerd-yolo` as source | Agent path only |
| No other tailnet nodes reachable | Implicit default-deny — add no second grant | No lateral movement |
| No LAN pivot via Pi | Pi advertises no routes; `tag:racknerd-yolo` appears in no `autoApprovers` and in no grant naming a CIDR | No `192.168.x.x` from the VPS |
| Mitch laptop → `tag:mpe-pi`:22 | Existing grant, unchanged | Admin unchanged |

### Phase 1 execution log (2026-08-16) — ⚠️ ACL NOT YET ENFORCING

| Node | State |
|---|---|
| `racknerd-99e2417` | `100.80.219.21`, `tag:racknerd-yolo`, routes **None**, `--shields-up=true`, Tailscale 1.102.2 |
| `mpe-pi` | `100.76.137.72`, `tag:mpe-pi`, routes **None**, Tailscale 1.102.2 (Debian trixie, aarch64) |

Probe results from Racknerd:

| # | Probe | Required | Actual |
|---|---|---|---|
| 1 | `nc -z 100.76.137.72 22` | succeed | ✅ succeeded |
| 2 | `nc -z 100.76.137.72 8099` **with a live listener** | **fail** | 🔴 **SUCCEEDED — port restriction not in effect** |
| 3 | `nc -z mpe-pi 22` (MagicDNS) | succeed | ✅ succeeded |
| 4 | `ping 192.168.1.210` (LAN) | fail | ✅ 100% loss |
| 5 | `PrimaryRoutes` on Racknerd | `None` | ✅ `None` |

**Probe 2 is the finding.** `grants` and `acls` are **additive** in Tailscale policy. A default policy carries `"acls": [{"action":"accept","src":["*"],"dst":["*:*"]}]`; adding the port-scoped grants alongside it changes nothing, because the wildcard already permits everything. **Delete the `acls` allow-all block** and keep only `tagOwners` + `grants`. Phase 1 does not exit until probe 2 fails.

This is exactly why the negative probe requires a **live listener** (below). Against a closed port, probe 2 would have failed for the wrong reason and been recorded as a pass — a false green indistinguishable from the real thing.

Not yet re-measured after the fix: probe 2, and the post-login xrun check (the tunnel now carries traffic; the earlier 0-xrun result was taken while `tailscaled` was installed but logged out, so it proves installation is harmless and nothing more). Connection is DERP-relayed (`relay "tor"`), not direct.

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
# After Layer 3 (run as claude-sandbox; the wrapper supplies the environment):
mpe-yolo ping             # must succeed
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
from="100.x.y.z",restrict,command="/usr/local/sbin/mpe-yolo-remote.sh" ssh-ed25519 AAAA... racknerd-yolo-2026-08
```

- **`restrict`** (OpenSSH ≥ 7.2) is deny-by-default for all forwarding/PTY/user-rc options and stays correct as OpenSSH adds new ones. It replaces the rev-2 `no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,no-user-rc` list, which was a hand-maintained allowlist of denials.
- **`from="100.80.219.21"`** bounds a leaked key to one source. Defense in depth behind the L1 ACL, not a substitute for it. **Filled in at Phase 1 exit, 2026-08-16:** Racknerd joined the tailnet as `racknerd-99e2417` (`racknerd-99e2417.tail6f6cf1.ts.net`), tagged `tag:racknerd-yolo`, advertising no routes, `--shields-up=true`. Tagged nodes do not key-expire, so no expiry action is needed.
- **Date the key comment** (`racknerd-yolo-2026-08`) so age is visible in `authorized_keys` without cross-referencing anything. Rotation remains manual (§Not in v1), and a visible date is what makes "manual" survivable.

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
| `logs-touch`, `logs-looper`, `logs-jackd`, `logs-watchdog` | `journalctl -u <unit> -n 200 --no-pager` — **fixed at `MPE_LOG_LINES_MAX` (200) lines** |
| `logs-surge` | **Not a journal tail.** Surge logs to a *file*: `commands/logs.sh` tails `${MPE_SURGE_LOG:-$HOME/surge-cli.log}`. The wrapper must hard-code that path — it must **not** honour a caller-supplied `MPE_SURGE_LOG`, and must not accept the variable over the wire at all |
| `test-pi-<suite>` — one per name in `MPE_TEST_SUITE_NAMES` | Fixed suite→module list (§Layer 3) |
| Phase B only: `deploy-dev`, `deploy-yolo-<slug>` | See Phase B branch enum (§Layer 3) |

**`-n` does not exist in the YOLO profile.** Earlier drafts listed `logs-surge-50` as a token and separately allowed `-n` capped at 200, which implied either ~1000 literal tokens or an argument parser — the second reopening the injection surface the token map exists to close. The agent has no need for a tunable tail: it gets 200 lines or it asks Mitch. `mpe logs <unit> -n N` remains available on the **laptop** profile, where `mpe_cli_clamp_log_lines` handles it.

New tokens require a change in **both** `mpe-yolo-remote.sh` and `mpe-cli`, plus a spec revision. Adding a token is a security change, not a feature.

### Wrapper test harness (required — the boundary cannot be the untested part)

Rev 2 verified the wrapper with a block of manual `ssh` commands a human runs once, while verifying the *guard* — explicitly labelled mistake-prevention — with an automated probe harness. That is exactly backwards.

**Deliverable (Phase 2): `tests/test_mpe_yolo_remote.py`, run on the Pi.** It invokes `/usr/local/sbin/mpe-yolo-remote.sh` directly with `SSH_ORIGINAL_COMMAND` set, so it needs no SSH round trip and can run from cron:

| Case | Assertion |
|---|---|
| Each token in the map | exit 0, expected command shape |
| `ping; id`, `logs-surge -n 5000`, `bash`, `bash -s`, `deploy-all.sh`, `""` | exit 1, **and** the offending token appears verbatim in the log |
| stdin payload present (`id`) with a valid token | token runs, payload never executes, **process returns promptly** (assert with a timeout — a hang is the failure mode `exec </dev/null` exists to prevent) |
| Token set as a whole | **golden-file comparison** — any addition or removal fails the test |

The golden-file case is what turns "adding a token is a security change, not a feature" from a sentence into a mechanism. Without it, that sentence is enforced by memory.

### Concurrency lock (required — one YOLO session, enforced)

The wrapper takes an **`flock`** on `/var/lock/mpe-yolo.lock` for the duration of any `test-pi-*` or deploy token, failing fast (non-blocking, exit 1, logged) rather than queueing.

Two problems, one line:

- The "one YOLO session at a time" invariant (§Not in v1) stops being unenforced.
- An agent in a retry loop cannot pile concurrent `test-pi-all` runs onto hardware Mitch also uses — a self-DoS on the appliance that needs no malice, just a failing test and a stubborn retry.

Read tokens (`ping`, `status`, `logs-*`) do not take the lock; they are cheap and non-mutating.

### sudoers (Mitch-only gate — specify before Phase 2)

Add a **narrow** sudoers drop-in (not `NOPASSWD: ALL`). Example intent:

- Allow `mitch` to run specific wrapper-invoked commands only (unittest, `configure-pi-paths.sh`, journalctl tails).
- **Reject** open `bash`, `apt`, `systemctl edit`, `deploy-all.sh` via sudo.

Get this wrong and Layer 2 becomes a root shell — review before apply.

### `mpe-agent` — the run-as user (rev 5: this replaces the Phase A invariant)

Agent code runs on the appliance by design. Containment therefore happens **below** the code, at the identity that executes it. This is the load-bearing control of the whole document.

| Property | Requirement |
|---|---|
| User | Dedicated **`mpe-agent`**, created for this purpose. Not `mitch`, not `mpe-yolo` (that is the SSH identity; this is the execution identity) |
| Checkout | Its own clone/worktree, owned by `mpe-agent`. Mitch's `~/MPE-Module` stays free to switch branches — the appliance dev loop is unchanged |
| sudo | **None**, except a narrow drop-in for specific `systemctl restart <unit>` calls if tests must bounce a service. Never `bash`, `apt`, `sed`, or `systemctl edit` |
| Reads | **No** access to `mitch`'s home, `/etc/mpe/`, `authorized_keys`, or any key material |
| Groups | `audio` (and whatever JACK/GPIO access the tests genuinely need). This is the one real grant and it is unavoidable — the point is hardware testing |
| Wrapper | `mpe-yolo-remote.sh` runs deploy and test tokens as `sudo -u mpe-agent`, **not** `sudo -u mitch` |

**Why this and not the invariant:** options that pin refs or separate worktrees restate "agent code must not run as `mitch`" as a procedure someone has to remember. This removes the condition — agent Python executes as a user that cannot reach Mitch's credentials, keys, or sudoers, no matter what the code does.

**It also fixes the laptop.** Local agents currently deploy and test as `mitch` on this appliance. Routing them through `mpe-agent` too reduces exposure that already exists, independent of Racknerd. Best return in this document.

### Performance-mode interlock (rev 5 — required)

One Pi, one sound card. The `flock` above prevents two agent sessions colliding; nothing prevented an agent starting `test-pi-all` while Mitch is playing.

| Item | Requirement |
|---|---|
| Marker | Root-owned `/etc/mpe/performance-mode` (or a state file the audio profile scripts already own), **not writable by `mpe-agent`** |
| Behaviour | `mpe-yolo-remote.sh` rejects **all** `test-pi-*` and deploy tokens with a distinct exit code and log line while the marker is present. Read tokens (`ping`, `status`, `logs-*`) remain allowed — they are cheap and non-mutating |
| Wiring | Set/cleared by the existing audio profile path (`set-audio-profile.sh`), so entering performance mode is one action Mitch already takes |
| Test | Wrapper suite asserts a `test-pi-*` token is refused with the marker present |

This is the same shape as the `flock`, with Mitch as the other lock holder.

### Credentials on the appliance (rev 5 — done, record kept)

Audited 2026-08-16. The Pi held a classic PAT at `/etc/mpe/git-credentials` (`root:mitch`, mode 640, scopes `public_repo, repo:status, repo_deployment`) — readable by `mitch`, and therefore by any agent code running as `mitch`. `public_repo` is **write** access to every public repo on the account.

`MPE-Sound-Module` is public, so `git pull` needs no credential at all. **Removed** rather than scoped down: capability absent beats capability forbidden.

- No SSH private keys on the Pi (`~/.ssh` holds only `authorized_keys`), and `known_hosts` is empty — the appliance has never connected out anywhere
- Token revoked; `credential.helper` entries unset (local + global); push URL disabled
- [`scripts/setup-pi-github-pat.sh`](../scripts/setup-pi-github-pat.sh) and [`docs/PI-GITHUB-ACCESS.md`](PI-GITHUB-ACCESS.md) **must be updated**, or a rebuild reinstates the credential
- **Do not** replace it with a deploy key. An HTTPS remote on a public repo needs nothing

### The Phase A invariant (VOID — retained as a record of why it cannot work)

> **Rev 5:** everything in this section is superseded by `mpe-agent` above. It is kept because the reasoning explains why ref-based approaches fail, and because rev 3 and rev 4 both treated it as load-bearing.
>
> It was already contradicted by the codebase when written: [`deploy-all.sh:29`](../scripts/deploy-all.sh#L29), [`deploy-boot-animation.sh:16`](../scripts/deploy-boot-animation.sh#L16), [`deploy-crash-fixes.sh:16`](../scripts/deploy-crash-fixes.sh#L16) and `mpe looper deploy` all `git pull` on the Pi, and the appliance was found sitting on a live feature branch (`apc-faders-loop-mix`) with untracked fixtures. The invariant was holding by coincidence, not by design — and under the rev 5 goal it must not hold at all.

#### Original text (superseded)

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

**Session wiring:** **not** an export in `claude-yolo.sh` — that is an ordinary environment variable in a session the agent controls. The pointer is supplied by the root-owned `/usr/local/bin/mpe-yolo` wrapper via `env -i` (below), which is the only sanctioned entrypoint. Never overwrite laptop `~/.config/mpe/mpe.env`.

#### Config loading is the vulnerability (required — and partly independent of this spec)

Rev 2 framed this as "pin the pointer as well as the file." That was necessary but not close to sufficient. `mpe_cli_load_config` has three separate defects, and the first one is a live remote-code-execution bug that exists today with or without Racknerd.

##### 🔴 `PI_MPE_MODULE` is remote command injection — fix now, do not wait for Gate A

```bash
mpe_cli_load_config() {
    if [ -f "$MPE_CLI_CONFIG" ]; then
        set -a; source "$MPE_CLI_CONFIG"; set +a
    fi
    PI_MPE_MODULE="${PI_MPE_MODULE:-}"      # preserves the caller's env value
}
```

`PI_MPE_MODULE` flows into `mpe_cli_pi_source_line`, which is interpolated **unquoted** into the `bash -s` heredoc in `lib/ssh.sh`. Verified by execution:

```bash
$ PI_MPE_MODULE='/tmp/pwn; id #'   # → mpe_cli_pi_source_line emits:
source /tmp/pwn; id #/scripts/lib/paths.sh
```

That runs on the Pi, as `mitch`, in **every** `mpe_cli_remote_bash` call — which is most of `commands/logs.sh`, `commands/test.sh`, and `commands/looper.sh`. It is reachable from the laptop profile today; Racknerd merely adds a second caller. **This is a `mpe-cli` bug, not a spec deliverable.** Fix it on its own schedule: validate `PI_MPE_MODULE` against a path charset (no `;`, no spaces, no `$`, no backticks) and quote the interpolation.

Note for the record: `MPE_SURGE_LOG` is **not** a comparable vector — `commands/logs.sh` escapes it so it expands on the Pi from the Pi's environment. Rev 3 checked; it is clean.

##### 🔴 A missing config file is a silent no-op, so the environment supplies the target

The `[ -f ]` guard has no `else`. If the blessed path is absent, unreadable, or simply renamed, `mpe_cli_require_config` is satisfied by inherited environment variables. Verified by execution:

```bash
MPE_CLI_CONFIG=/nonexistent PI_USER=evil PI_HOST=evil.host SSH_KEY=/etc/hostname
# → mpe_cli_require_config passes cleanly; every var is non-empty, SSH_KEY exists
```

So a command line carrying the blessed literal path — satisfying any guard rule that checks for it — can still target an arbitrary host with an arbitrary key. **Fix:** a config path that does not exist or is not readable is a hard error, not a fallthrough.

##### 🟡 The root-owned-config check as written in rev 2 breaks the laptop

Rev 2 claimed a root-owned requirement "costs nothing" on the laptop. It costs everything: `~/.config/mpe/mpe.env` is `mitch`-owned, and the check bricks every laptop `mpe` command — directly contradicting this spec's own "Laptop: Mitch admin path unchanged" acceptance line.

The obvious repair fails too. "Root-owned **or** owned by the invoking user" is satisfied by `/tmp/x.env`, which `claude-sandbox` owns. **There is no owner-based predicate that is both laptop-safe and agent-proof**, because on the laptop the invoking user *is* the trust anchor and on Racknerd it is the adversary.

**Fix — stop pretending this is containment.** Gate it on the profile, and say plainly what it is worth:

- Root-owned Racknerd wrapper sets `MPE_CLI_REQUIRE_ROOT_CONFIG=1`; `mpe_cli_load_config` enforces root ownership and non-group/other-writability only when that is set.
- The agent can unset it. Per §Layer weight that is acceptable — this is mistake-prevention, not a boundary. Rev 2's "belt and braces, neither sufficient alone" was wrong: both "braces" live on the host the agent controls.

##### The wrapper, not the regex (replaces rev 2's literal-string guard rule)

Rev 2 required every allowlisted `mpe` call to carry the literal `MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env`. The guard is `grep -qE` over the raw command string, so a decoy assignment satisfies it:

```bash
: MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env; MPE_CLI_CONFIG=/tmp/x.env mpe ping
```

A prior `export` in an earlier tool call works as well. **Required instead:** a root-owned `/usr/local/bin/mpe-yolo` wrapper that the agent cannot edit:

```bash
#!/bin/bash
exec /usr/bin/env -i \
  PATH=/usr/bin:/bin HOME=/home/claude-sandbox \
  MPE_CLI_CONFIG=/home/claude-sandbox/.config/mpe/mpe-yolo.env \
  MPE_CLI_REQUIRE_ROOT_CONFIG=1 \
  /home/claude-sandbox/.local/bin/mpe "$@"
```

`env -i` clears the environment, which closes `PI_MPE_MODULE`, `PI_HOST`, `SSH_KEY`, and every future variable of the same shape in one move — a property no regex has. The guard then allowlists **one binary name** and denies bare `mpe`, which is a rule that can actually be written; "deny anything not matching an allowlist" over arbitrary shell **cannot** be (see §Layer 4).

Note the existing "deny writes under `.config/mpe/`" rule does **not** cover any of this — the attacks write somewhere else entirely, or nowhere at all.

##### 🔴 Install provenance — the wrapper above execs agent-writable code (rev 4)

The exec target in that wrapper is wrong as written. `mpe-cli`'s `install.sh` does:

```bash
BIN_DIR="${HOME}/.local/bin"
ln -sf "$REPO_ROOT/bin/mpe" "$BIN_DIR/mpe"      # symlink into a git clone
```

and `bin/mpe` resolves its own libraries relative to the **resolved symlink**:

```bash
MPE_CLI_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
source "$MPE_CLI_ROOT/lib/config.sh"            # …ssh.sh, validate.sh
```

So `/usr/local/bin/mpe-yolo` — root-owned, unforgeable, `env -i` — hands control to a symlink into a clone `claude-sandbox` owns. **`lib/config.sh` is the file that enforces `MPE_CLI_REQUIRE_ROOT_CONFIG`.** The pin is enforced by code the agent it pins can rewrite, which is §Layer weight consequence 2 applied to the guard but not to this.

Per §Layer weight the agent editing L3 is *acceptable* — but then `MPE_CLI_REQUIRE_ROOT_CONFIG` must not be carried in the acceptance checklist as a verified control. **Pick one.** This spec picks the fix, because it is cheap:

| Item | Requirement |
|---|---|
| Install location | Root-owned **`/usr/local/lib/mpe-cli`** (clone + `bin/` + `lib/`), not writable by `claude-sandbox`. Wrapper execs `/usr/local/lib/mpe-cli/bin/mpe` — **not** `~/.local/bin/mpe`, and not a symlink through agent-owned space |
| `PATH` | Drop `~/.local/bin` from the YOLO profile's `PATH` so a shadowing `mpe` cannot be planted |
| **Do not run `install.sh` on Racknerd** | It `mkdir -p`s `~/.config/mpe` and seeds `mpe.env` **mode 600 owned by the invoking user** — at the exact path `lib/config.sh` defaults to. Running it as `claude-sandbox` manufactures the agent-owned config this layer exists to prevent. Racknerd install is a documented root procedure, not `install.sh` |
| Version pin | `bootstrap-nerdrack.sh` asserts `mpe version --check <N>` and fails the session if older |

##### 🟡 Version pinning (rev 4) — the acceptance checklist is otherwise unfalsifiable

`mpe-cli` already ships the mechanism, and `bin/mpe` says what it is for:

```bash
# Bump on any change consuming repos may pin against: new subcommand, new test
# suite, or changed exit-code contract.
MPE_CLI_VERSION="1.2.2"
```

`mpe version --check X.Y.Z` exits 0 if ≥, 1 if older, 2 on bad input. Nothing in rev 3 used it. Without it, "`PI_MPE_MODULE` injection closed and unit-tested" is verified **on the laptop** and says nothing about the binary Racknerd actually runs — which, per the item above, was until now an agent-writable clone. Bump the minor version on the token-dispatch deliverable and pin that version in bootstrap.

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

**Also fix the base validator, not just the new enum.** `mpe_cli_validate_git_branch` is `^[a-zA-Z0-9/_.-]+$`, which matches `../../etc/passwd` and `/etc/x` — a traversal primitive anywhere a branch string reaches a path. No Phase A path reaches it, so this is not a Phase A blocker, but fix it in `lib/validate.sh` rather than papering over it one caller at a time.
| `git checkout` on Pi | **`pi_soak`** | Only via deploy token, not raw git |

**Phase B threat — test after deploy:** Agent can merge code to `yolo/*`, deploy to Pi, then run `mpe test pi` — unittest executes **as `mitch`**. Mitigations (pick before enabling Phase B):

1. Run Pi tests only against a **fixed ref** (e.g. `dev`) while deploy uses a separate worktree path, **or**
2. Require Mitch soak on branch before `pi_soak` clear includes deploy+test, **or**
3. **(Recommended)** Run tests as a dedicated unprivileged user (larger change).

Option 3 is recommended because options 1 and 2 restate the Phase A invariant (§Layer 2) as procedure, while 3 **removes** it: agent-authored Python then executes as a user with no access to `mitch`'s home, keys, or sudoers. Options 1 and 2 leave "arbitrary code as `mitch`" one operator mistake away, which is the same failure mode Phase A already carries and Phase B multiplies.

Document chosen mitigation in DECISIONS.md before Phase 6.

**Still denied on Racknerd (all phases):**

- `mpe restart *`
- **`mpe looper` mutating subcommands** — `deploy` (Phase A), `debug`, `enable`, `disable`, `buffer`. These already issue `sudo systemctl restart …` and `sudo bash -c 'sed -i … /etc/mpe/mpe.env'` directly (`commands/looper.sh`). Not reachable in Phase A because the subcommand is not allowlisted, but name them here so nobody widens the allowlist casually
- `deploy-all.sh`, profile scripts (direct or via SSH)
- Raw **`ssh`, `scp`, `rsync`** (any host)
- Edits to **`~/.config/mpe/`** (any file)
- Edits to `/etc/mpe/mpe.env` on Pi

Implement new capability as **mpe-cli subcommands** per appliance-cli pattern — not SSH allowlist widening.

---

## Layer 4 — YOLO guardrails (Racknerd)

The guard is **allow-by-default** — `yolo-shell-guard.sh` runs a list of deny rules and ends in `allow`. Pi access requires **new deny rules**, not bolting an allowlist onto the final `allow`.

**Scope reminder:** this layer stops a cooperative agent from making a mistake. It does not stop `ssh -i <key>` called from inside a Python script (the hook sees Bash tool calls only), and — until the ownership fix below lands — it does not stop the agent editing the guard itself. Budget review time accordingly: **very little of it belongs here.**

### 🔴 Guard ownership (prerequisite for every other Layer 4 item)

Every change in the next table is a change to `scripts/yolo/yolo-shell-guard.sh` — a git-tracked file inside the repo the agent edits all day. Verified: `sed -i s/x/y/ scripts/yolo/yolo-shell-guard.sh` → `allow`. The hook that invokes it is wired in `.claude/settings.local.json`, gitignored and inside `$ROOT`, loaded via `claude-yolo.sh`'s `--setting-sources project,local`.

Writing better regexes into an agent-editable file is not a security change. Required first:

| Item | Requirement |
|---|---|
| Guard script | Root-owned at an absolute path outside `$ROOT` — **`/usr/local/sbin/yolo-shell-guard.sh`**, mode 0755, not writable by `claude-sandbox`. The in-repo copy becomes a *source* artifact that Mitch installs, not the file the hook runs |
| Hook config | Settings file outside `$ROOT`, root-owned, referencing the absolute guard path. Drop `local` from `--setting-sources` on Racknerd if a project-local settings file can still override the hook |
| Drift check | `check-guardrails.sh` asserts the installed guard's hash matches the repo copy — otherwise a stale root-owned guard silently diverges from the reviewed one |
| **Launcher + gate scripts** (rev 4) | `claude-yolo.sh` invokes `check-yolo-gates.sh`, `check-guardrails.sh`, `check-mcps-headless.sh`, `check-backpressure.sh` — **all five in-repo and agent-writable**. Rev 3 moved the gates' *inputs* out of the tree and left the caller in it: deleting four lines from the launcher removes every gate at once, and `check-guardrails.sh` is itself the drift-checker that validates the root-owned guard. Install all five root-owned alongside the guard, or the drift check is self-attesting |

Note this requires a *prior* session to have edited them — the running session's gates have already fired. That is not a mitigation: per §Layer weight, editing repo files is the agent's job, and YOLO sessions are serial.

Without this, treat every row below as documentation of intent rather than enforcement, and do not count it toward Gate A.

### 🔴 Fail closed — the guard emits no verdict when it breaks (rev 4)

`yolo-shell-guard.sh` is `set -euo pipefail`, parses its input with `python3`, and every deliberate exit is `0`-with-JSON. There is no intentional non-zero exit — so **every non-zero exit is a crash**, and a `PreToolUse` hook that exits non-zero without a verdict is a non-blocking error: the Bash call proceeds. Executed:

```bash
$ echo 'not json' | YOLO_HOOK_AGENT=claude bash scripts/yolo/yolo-shell-guard.sh
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
exit=1          # ← no verdict emitted at all
```

**Today this is near-unreachable** — Claude Code constructs the payload, so the agent does not control its well-formedness (`{"tool_input":{"command":null}}` yields a clean `allow`, exit 0). It is listed as 🔴 because **this spec makes it reachable**: the session gate design below has the guard read and parse `/var/lib/mpe-yolo/session-gates.json` **on every Bash hook**. Under `set -e`, a missing, truncated, or mid-write file does not deny — it kills the guard and lets the command through. "Guard denies if the file is missing" becomes "guard allows if the file is unreadable," which is the exact inversion of the stated rule.

It also evades the harness by construction: `probe_deny` inspects the JSON verdict, and a crashed guard emits no JSON to inspect — so this failure mode reads as an infrastructure hiccup, never as an open gate.

**Required before the session gate file ships:**

| Item | Requirement |
|---|---|
| Trap | `trap 'deny "guard error"' ERR` (and on unexpected `EXIT` with non-zero status) — an exception denies, it does not vanish |
| Gate read | Explicit failure branch: unreadable / unparseable / missing gate file → `deny`, never a bare `set -e` abort |
| Probes | `check-pi-access-lockdown.sh` asserts deny with the gate file **deleted**, **truncated**, and **corrupt** — and asserts the guard emitted a verdict at all, not merely that the command failed |

### Required guard changes

| Change | Rationale |
|---|---|
| Deny **all** raw `ssh`, `scp`, `rsync` (any host) | Replace the current `raspberrypi` hostname match — today `ssh mitch@mpe-pi` **allows** |
| Allowlist the **`mpe-yolo` wrapper only**; deny bare `mpe` and `/path/to/mpe` | Replaces rev 2's "deny anything not matching an allowlist regex", which is not expressible (see below). Also closes the path-prefix bypass — today `(^|[;&|[:space:]])mpe[[:space:]]+restart` does not match `/home/claude-sandbox/.local/bin/mpe restart` |
| Deny writes to the **installed guard path and hook config** | Cheap backstop once ownership is fixed; the permissions are the real control |
| Fix `rm -rf` flag-order gap: `rm -fr /etc/foo` currently returns **allow** | Guard rule requires `r` before `f`. Also unhandled: `rm -r -f /`, `rm --recursive --force /` |
| Deny writes to **`.config/mpe/`** | Config `source` under `set -a` is code execution + host retarget |
| Deny **`--allow-partial`** on any `mpe test` invocation | Spec forbids it on Racknerd; nothing enforced it before |
| Deny **`mpe restart`** (keep existing, fix path-prefix) | Mitch-only |
| Deny Phase B deploy tokens unless **session gate file** shows `pi_soak` cleared | Per-command enforcement — not session-start-only |

**Why "deny unless allowlisted" is not a rule you can write here.** The guard greps a raw command string with no shell parsing — hence the `[^;|]*` hacks throughout the existing rules. Verified bypasses of the current deny rules, all returning `allow`:

```bash
bash -c 'mpe restart surge'      # allow — subshell string not decomposed
m""pe restart surge              # allow — quote splitting defeats the literal
rm -fr /etc/foo                  # allow — flag-order gap
echo "onecli agents"             # deny — false positive: matches inside a quoted string
```

The last line is the same defect pointing the other way, and it means the guard will block an agent merely *writing documentation about* the guard. Raw-string matching produces both failure modes at once; that is the argument for the `env -i` wrapper (§Layer 3), which does not depend on parsing at all.

### Gate state must leave the agent-writable tree (🔴 — supersedes rev 2's gate file design)

`check-yolo-gates.sh` validates gates once at session start and **exits leaving no artifact**, so nothing at Bash-hook time can know whether `pi_soak` was cleared. Rev 2 proposed a root-owned session gate file to close that. The file was unforgeable; **everything it derived from was not.**

Three agent-writable inputs, each sufficient on its own to defeat the gate:

| Input | Path | Effect |
|---|---|---|
| **Nerdrack marker** | `$ROOT/.claude/settings.local.json` (gitignored) or `$ROOT/.yolo/headless` | `is_nerdrack()` returns false → `check-yolo-gates.sh` prints "laptop mode: skipped" and **exits 0 without checking any gate**. Delete one file and the entire gate system is off |
| **Queue** | `$ROOT/.claude/primitives/yolo-queue.json` (`mitch`-owned, in-tree) | Agent writes `status: ready`, `spec_approved: true`, cleared `human_gates`. The rev-2 helper "derives cleared gates itself" — from this file |
| **Spec approval** | this document, in-tree | `check-yolo-gates.sh` greps `^\*?Status:\*?\s*Approved`. **The agent can approve its own spec** |

The guard denies `enqueue-yolo-task.sh (approve|clear-gate)`, but nothing stops `python3 -c` writing the JSON directly — and per §Layer weight, nothing on Racknerd could.

**Required:** move all three outside the agent's write path before any of this is called a gate.

| Item | Requirement |
|---|---|
| Nerdrack marker | Root-owned `/etc/mpe-yolo/headless`, not in `$ROOT` |
| Queue | Root-owned `/var/lib/mpe-yolo/queue.json`, synced from the laptop by Mitch; `check-yolo-gates.sh` reads that path on Racknerd |
| Spec approval | Approval recorded in the root-owned queue entry (spec path + content hash), **not** grepped out of a working-tree Markdown file |
| Task identity | Derived from the root-owned queue, **not** from `YOLO_TASK_ID` — that is agent-set environment, and rev 2's staleness rule compared the gate file against exactly it |

### Session gate file (given the above)

**It must not be forgeable by the agent** — and neither may its inputs. A file at `~/.cache/mpe-yolo/session-gates.json` mode 600 is *owned by `claude-sandbox`*: the agent writes its own permission slip. Required instead:

| Property | Requirement |
|---|---|
| Location | **`/var/lib/mpe-yolo/session-gates.json`** — directory root-owned, mode **0755**, file **0644** |
| Writer | `check-yolo-gates.sh` via a small root-owned setuid-free helper invoked through a **narrow sudoers entry** (`claude-sandbox` may run *that one script*, nothing else) |
| Reader | `yolo-shell-guard.sh`, on every Bash hook |
| Content | Cleared gate names for the **current `YOLO_TASK_ID`**, plus the task id itself |
| Staleness | Guard denies if the file's task id ≠ the task id in the **root-owned queue**, or if the file is missing. **Not** `YOLO_TASK_ID` — that is agent-set |

The writer helper takes **no arguments from the agent**: it re-reads the **root-owned** queue and derives cleared gates itself. The agent can trigger a refresh; it cannot dictate the contents. (Rev 2 said the same sentence about the *in-tree* queue, where it was false.)

If the sudoers helper and the root-owned queue are judged not worth it for v1, the honest alternative is to **not ship Phase B at all** — an agent-writable gate chain is worse than no gate, because it reads as enforcement in every subsequent review.

**Verification (Phase 4 exit — see phase table; rev 2 said "Phase 6" here and "Phase 4" in the table):** prove **denial** with `pi_soak` uncleared, and prove the agent cannot flip it at any link in the chain:

```bash
echo '{"pi_soak":true}' > /var/lib/mpe-yolo/session-gates.json   # must fail on permissions
echo '{}' > /var/lib/mpe-yolo/queue.json                         # must fail on permissions
rm /etc/mpe-yolo/headless                                        # must fail on permissions
```

### `scripts/yolo/check-pi-access-lockdown.sh`

Mirror `check-onecli-lockdown.sh` pattern. Include **negative** probes:

**Deny probes:**

- `ssh mitch@mpe-pi`, `scp x mitch@mpe-pi:`, `rsync x mitch@mpe-pi:`
- `ssh mpe-yolo@mpe-pi ping` — raw ssh denied even on the *sanctioned* path; `mpe-cli` is the only entrypoint
- `deploy-all.sh`, `mpe restart surge`
- `/home/claude-sandbox/.local/bin/mpe restart surge` (path-prefix)
- `bash -c 'mpe restart surge'`
- `echo x > ~/.config/mpe/mpe-yolo.env`
- `mpe ping`, `/home/claude-sandbox/.local/bin/mpe ping` — bare CLI; only the `mpe-yolo` wrapper is allowlisted
- `mpe-yolo test pi audio --allow-partial`
- `mpe-yolo logs surge -n 5000` — `-n` not in YOLO profile
- `bash -c 'mpe restart surge'` and `m""pe restart surge` — **known-failing today**; both return `allow`
- `rm -fr /etc/foo` — **known-failing today**; flag-order gap
- `sed -i s/x/y/ /usr/local/sbin/yolo-shell-guard.sh` — guard self-edit
- Phase B deploy command with session gate file **without** `pi_soak`

**Allow probes that must stay allowed (false-positive regression):**

- `echo "notes about the onecli guard"` — **known-failing today**: returns `deny` because rules match inside quoted strings. Fix or accept explicitly; do not leave it undocumented

**Filesystem probes — `probe_deny` cannot observe these.** `probe_deny` inspects only the guard's JSON verdict, so permission-based controls need a separate assertion helper (`probe_write_fails`) in the same script. Rev 2 listed these among the guard deny probes, where they would have passed vacuously:

- `echo '{"pi_soak":true}' > /var/lib/mpe-yolo/session-gates.json`
- `echo '{}' > /var/lib/mpe-yolo/queue.json`
- `rm /etc/mpe-yolo/headless`
- `sed -i s/x/y/ /usr/local/sbin/yolo-shell-guard.sh` (permission check, distinct from the guard rule above)

**CLI-behaviour probes — also not guard probes.** `MPE_CLI_CONFIG=/tmp/x.env mpe ping` being *refused by `mpe-cli` itself* is a `mpe-cli` unit test, not a hook probe. Assert it there; the acceptance checklist demands both paths and only one of them is observable here.

**Allow probes (Phase A enabled):**

- `mpe-yolo ping`
- `mpe-yolo test pi audio`
- `mpe-yolo logs surge`

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
> **Rev 5:** this table is **build order**, not scope. The Phase A/B split described a boundary that no longer exists (§Revision 5). Phase 6 is not optional — it is the goal. Phases are retained because the ordering is still correct.

| **0 — Gate A** | This spec approved | Mitch approves; **`mpe-agent` design signed off** as the containment story |
| **1 — Tailscale** | Pi + Racknerd on tailnet; ACL deployed | TCP `:22` OK; other ports/LAN unreachable; **live-listener negative probe passes**; `--shields-up` on Racknerd only; xrun baseline unchanged (§Layer 1) |
| **2 — Pi SSH** | `mpe-yolo` user + `mpe-yolo-remote.sh` (fixed token table, `exec </dev/null`, `from=`/`restrict` key, `flock`) + narrow sudoers + **`tests/test_mpe_yolo_remote.py`** | No interactive shell; **stdin discarded, never hangs**; full-string token match; audit log incl. rejected tokens; no `bash -s` path; **wrapper test suite green, golden token file committed** |
| **3 — mpe-cli** | `PI_MPE_MODULE` injection fix + hard-error on missing config + **root-owned Racknerd install at `/usr/local/lib/mpe-cli` (not `install.sh`, not `~/.local/bin`)** + **version bump + pin** + root-owned yolo env + **token dispatch** + profile-gated root-config check | `PI_MPE_MODULE='/tmp/x; id #'` no longer reaches the wire (**unit test**); `MPE_CLI_CONFIG=/nonexistent PI_HOST=evil mpe ping` exits non-zero; `mpe-yolo test pi audio` reaches Pi; **no heredoc stdin** on wire; **`mpe version --check <N>` asserted in bootstrap**; **wrapper exec target and its `lib/` not writable by `claude-sandbox`**; **laptop `mpe` still works** |
| **4 — Guards** | **Root-owned guard + hook config + queue + nerdrack marker + `claude-yolo.sh` + gate scripts** (prerequisite), then **fail-closed trap**, shell-guard rule updates, session gate file, `check-pi-access-lockdown.sh` | `check-guardrails.sh` green; installed-guard hash matches repo copy; all deny probes deny; **all four forgery writes fail on permissions**; `rm -fr`, `rm -r -f`, and `bash -c` gaps closed; **guard denies on deleted/corrupt gate file and always emits a verdict** |
| **5 — YOLO smoke** | Queued task: run Pi test suite only | YOLO log shows Pi test run; exit ≠ 3; no deploy; **Phase A invariant asserted** — Pi checkout ref is Mitch-controlled and carries no agent-authored commit |
| **6 — Deploy (opt.)** | `mpe looper deploy` + `pi_soak` session gate | Supervised deploy with gate cleared; **deny probe** with gate uncleared |

**Phase B (deploy + test-after-deploy mitigation)** requires a **separate Gate A amendment** after Phase A soak — do not bundle with initial approval.

**Rollback (complete — corrected in rev 4; rev 3 listed none of its own root-owned artifacts):**

*Pi:*

1. Remove Racknerd from tailnet / revert ACL
2. Remove `mpe-yolo` `authorized_keys` entry (or delete `mpe-yolo` user)
3. Remove `/usr/local/sbin/mpe-yolo-remote.sh`, sudoers drop-in, `/var/lock/mpe-yolo.lock`, logrotate rule (keep `/var/log/mpe-yolo-remote.log` for audit)

*Racknerd:*

4. Remove `/usr/local/bin/mpe-yolo` wrapper and root-owned `/usr/local/lib/mpe-cli`
5. Remove root-owned `mpe-yolo.env`
6. Restore `yolo-shell-guard.sh`, hook config, `claude-yolo.sh`, and gate scripts to in-repo copies; remove `/usr/local/sbin/yolo-shell-guard.sh` and the out-of-`$ROOT` settings file
7. Remove `/etc/mpe-yolo/headless`, `/var/lib/mpe-yolo/` (queue + session gates), and the sudoers entry for the gate-writer helper

**Ordering matters:** do step 6 *last* on Racknerd. Reverting the guard to the repo copy while a root-owned guard is still installed is the silent-divergence state the drift check exists to catch — and the drift check would then fail on a system nobody considers in scope.

YOLO returns to unit-test-only on VPS.

**Not in v1:**

- ~~**Concurrent session locking**~~ — **moved into v1** as an `flock` in the Pi wrapper (§Layer 2). It also bounds agent retry loops against shared hardware, which made it too cheap to defer.
- **Key rotation on Racknerd rebuild** — the rollback procedure covers revocation; rotation is manual until documented in DECISIONS. Mitigated slightly by dating the `authorized_keys` comment (§Layer 2) so age is visible. Add a revoke-on-suspicion trigger to the rollback list before Phase B.
- **False-positive guard matches inside quoted strings** — documented as a known-failing allow probe (§probe list) rather than fixed.

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
- [ ] Pi: **`tests/test_mpe_yolo_remote.py` green**, golden token file committed — the boundary is tested, not just described  
- [ ] Pi: `authorized_keys` uses `restrict` + `from="<racknerd tailnet IP>"`, key comment carries its creation date  
- [ ] Pi: `flock` held for `test-pi-*`; second concurrent invocation fails fast and is logged  

**Prerequisite code fix (not gated on this spec)**

- [ ] `mpe-cli`: `PI_MPE_MODULE` injection closed and unit-tested — `PI_MPE_MODULE='/tmp/x; id #'` cannot reach the remote heredoc  
- [ ] `mpe-cli`: missing/unreadable `MPE_CLI_CONFIG` is a hard error; env-supplied `PI_HOST`/`PI_USER`/`SSH_KEY` cannot stand in for it  

**Ownership (must land before any L3/L4 item counts)**

- [ ] Racknerd: guard installed root-owned at `/usr/local/sbin/`, hook config outside `$ROOT`, installed-vs-repo hash checked  
- [ ] Racknerd: queue at `/var/lib/mpe-yolo/queue.json`, nerdrack marker at `/etc/mpe-yolo/headless`, both root-owned  
- [ ] Racknerd: spec approval read from the root-owned queue, **not** grepped from a working-tree file  
- [ ] Racknerd: **`claude-yolo.sh` and all four gate scripts root-owned** — the launcher that invokes every gate is not agent-writable (rev 4)  
- [ ] Racknerd: **`mpe-cli` installed root-owned at `/usr/local/lib/mpe-cli`**; `mpe-yolo` wrapper execs that path; `~/.local/bin` absent from the YOLO `PATH`; `install.sh` **not** used on Racknerd (rev 4)  
- [ ] Racknerd: **no agent-owned `~/.config/mpe/mpe.env`** exists at the `lib/config.sh` default path (rev 4)  
- [ ] Racknerd: **`mpe version --check <N>` asserted in `bootstrap-nerdrack.sh`**, pinned at the token-dispatch release (rev 4)  

**Phase A invariant**

- [ ] Pi checkout ref is Mitch-controlled and carries **no agent-authored commit** (§Layer 2)  

**Mistake-prevention (L3–L4) — real, but not containment**

- [ ] Racknerd: `mpe-yolo ping` → OK (root-owned `env -i` wrapper is the only entrypoint)  
- [ ] Racknerd: `mpe-yolo test pi audio` → reaches Pi; **exit 0 only if full suite ran** (exit 3 = fail)  
- [ ] Racknerd: bare `mpe ping` and `/path/to/mpe ping` → guard deny  
- [ ] Racknerd: `mpe-yolo test pi audio --allow-partial` → guard deny  
- [ ] `mpe-cli` unit test: `MPE_CLI_CONFIG=/tmp/x.env` refused when `MPE_CLI_REQUIRE_ROOT_CONFIG=1` (**not** a guard probe — see §probe list)  
- [ ] Laptop regression: profile-gated root-config check does **not** fire on `mitch`-owned `~/.config/mpe/mpe.env`  
- [ ] Racknerd: `ssh mitch@mpe-pi` **and** `ssh mpe-yolo@mpe-pi ping` → guard deny (before SSH)  
- [ ] YOLO: `deploy-all.sh` → guard deny  
- [ ] YOLO: `mpe restart surge` and `/path/mpe restart surge` → guard deny  
- [ ] YOLO: `racknerd onecli` / raw `ssh` / `scp` / `rsync` → guard deny  
- [ ] YOLO: write to `~/.config/mpe/` → guard deny  
- [ ] YOLO: `bash -c 'mpe restart surge'`, `m""pe restart surge`, `rm -fr /etc/foo` → guard deny (**all three return `allow` today**)  
- [ ] YOLO: writes to `/var/lib/mpe-yolo/session-gates.json`, `/var/lib/mpe-yolo/queue.json`, `/etc/mpe-yolo/headless`, `/usr/local/sbin/yolo-shell-guard.sh` → **all fail on permissions**  
- [ ] YOLO: `rm -r -f /etc/foo` and `rm --recursive --force /etc/foo` → guard deny (**both return `allow` today**, alongside `rm -fr`) (rev 4)  
- [ ] Guard: **deleted, truncated, and corrupt** `/var/lib/mpe-yolo/session-gates.json` → **deny with a verdict emitted**, not a non-zero exit with empty stdout (rev 4)  
- [ ] Harness documents the `ssh`-from-Python blind spot as a passing `allow` probe  
- [ ] Harness documents **`agentjail` as a fail-open second hook** — not counted as a layer (rev 4)  

**Unchanged**

- [ ] Laptop: Mitch admin path unchanged (`mitch@pi`, `mpe restart`, `-n` on logs, heredoc path)  

---

## Documentation updates (on implementation)

- [`docs/local-vs-nerdrack-dev.md`](local-vs-nerdrack-dev.md) — Pi row, Phase A/B table  
- [`AGENTS.md`](../AGENTS.md) — Racknerd Pi rules vs laptop  
- [`Documents/DECISIONS.md`](../Documents/DECISIONS.md) — dated approval row  
- `mpe-cli` — **`PI_MPE_MODULE` injection fix (ship independently)**, hard-error on missing config, profile-gated root-config check in `lib/config.sh`, `..`/leading-`/` rejection in `mpe_cli_validate_git_branch`, token dispatch, yolo profile, branch enum, AGENTS.md allowlist strings (now `mpe-yolo`, not `MPE_CLI_CONFIG=… mpe`)  
- `mpe-cli` **README / `install.sh`** (rev 4) — document the **root-owned Racknerd install** as a separate procedure and warn that `install.sh` is laptop-only, because it seeds a user-owned `~/.config/mpe/mpe.env` at the default config path; **bump `MPE_CLI_VERSION`** on the token-dispatch change so bootstrap can pin it  
- `AGENTS.md` / `claude-yolo.sh` — guard and hook config move to root-owned absolute paths; `--setting-sources` reviewed so a project-local file cannot override the hook; **`claude-yolo.sh` and the four gate scripts install root-owned** (rev 4)  
- `bootstrap-nerdrack.sh` (rev 4) — assert `mpe version --check <N>`; verify the `mpe-yolo` wrapper's exec target is not `claude-sandbox`-writable  
- OM-Repo `Docs/appliance-cli-pattern.md` — Racknerd yolo profile subsection  

---

## Gate A approval

**Approver:** Mitch  
**Date:** _pending_  
**Scope approved:** **Phase A only** _(Phase B requires separate amendment after Phase A soak and Phase B mitigation decision)_

When approved, change **Status** line at top to `Approved` and record date here.

**Do not approve Phase A+B together** until session gate enforcement (§Layer 4) and Phase B test-after-deploy mitigation (§Layer 3) are implemented and probed.

**What Gate A is actually approving.** Rev 1 said four layers of defense. Rev 2 said two. Rev 3, after executing the code rather than reading it, says **one and a half**. Approving this means accepting:

1. The **Tailscale ACL** (L1) is the one control that holds unconditionally.
2. The **Pi token table** (L2) is the only thing standing between an agent with a working SSH key and the appliance — and it is *half* a layer until `tests/test_mpe_yolo_remote.py` exists, because an untested boundary is an asserted one. Reviewing the token map is the whole of Gate A's security value.
3. The **Phase A invariant** holds and is checked: the Pi's checkout carries no agent-authored commit, because `test-pi-*` runs Python as `mitch` (§Layer 2).
4. Racknerd's guard and CLI are **mistake-prevention that the agent can edit**, not merely bypass. Rev 2 conceded bypass-by-non-Bash-caller; rev 3 concedes that the guard script, its hook config, the queue it reads, and the marker that decides whether gates run at all are all files the agent writes in the normal course of its job. Rev 4 adds the **launcher that invokes every gate**, the **`mpe-cli` clone the sanctioned wrapper execs**, and — until pinned — **the version of that CLI**. The ownership fixes in §Layer 3 and §Layer 4 change that; **until they land, no L3/L4 item should be counted as a control in any review.**
5. **Nothing on the Pi has been reviewed by anyone, because none of it exists.** `mpe-yolo-remote.sh`, `tests/test_mpe_yolo_remote.py`, the sudoers drop-in, and the ACL are all specified and none are written. Four review passes have validated the *reasoning about* the boundary; zero have validated the boundary. Gate A approves a design, and Phase 2 exit — not this signature — is where the security actually gets checked.

If any of those reads as uncomfortable, that discomfort is the accurate one. **Three** framings in this document have now been wrong in the same direction — all three overcounted defense — so the prior should be that the remaining count is still generous, not harsh. Rev 4's specific lesson: each pass found the *previous* pass had applied its own best test one level too shallow. Rev 3 wrote "a control is only real if the agent cannot forge its input **or edit its implementation**" and then did not ask what `/usr/local/bin/mpe-yolo` executes. Expect rev 5 to find the same shape somewhere else.

**Ordering note.** The `PI_MPE_MODULE` injection (§Layer 3) is a live `mpe-cli` bug reachable from the laptop today. It is listed here for completeness but **must not wait on this gate** — fix and ship it independently.
