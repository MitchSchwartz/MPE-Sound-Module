# Pi GitHub access

**Status:** The appliance holds **no GitHub credential**. This is deliberate and is the correct state.

*Last updated: 2026-08-16 (America/Toronto) — replaces the classic-PAT procedure.*

**Related:** [`racknerd-pi-access-spec.md`](racknerd-pi-access-spec.md) · [`scripts/setup-pi-github-pat.sh`](../scripts/setup-pi-github-pat.sh) (deprecated, refuses to run)

---

## Current state

| Identity | GitHub access | How |
|---|---|---|
| **Laptop** (MitchSchwartz) | Full owner | SSH / `gh` |
| **Pi (`mitch`)** | **None** | `MPE-Sound-Module` is public — HTTPS pulls anonymously |
| **Racknerd** (agent) | Vaulted PAT | OneCLI `github-mpe-module`; the token never lands on disk |

Verify the Pi needs nothing:

```bash
ssh mitch@raspberrypi2.local 'git -C ~/MPE-Module fetch origin --dry-run'
```

Appliance git config, as of 2026-08-16:

```
origin  https://github.com/MitchSchwartz/MPE-Sound-Module.git (fetch)
origin  DISABLED (push)
credential.helper: unset (local and global)
~/.ssh: authorized_keys only — no private keys, known_hosts empty
```

`push` is deliberately disabled. The appliance is a deploy target; it has no reason to publish. This is mistake-prevention, not a control — `mitch` can reset the URL — but it costs nothing.

---

## What changed and why

The Pi previously held a **classic PAT** at `/etc/mpe/git-credentials` (mode 640, `root:mitch`), installed by `setup-pi-github-pat.sh`. Audited 2026-08-16 and removed. Three reasons, in order of weight:

1. **It was unnecessary.** `MPE-Sound-Module` is public. `git pull` over HTTPS needs no credential, so the token bought nothing.
2. **It granted write, broadly.** Live scopes were `public_repo, repo:status, repo_deployment`. **`public_repo` is write access to every public repository on the account** — classic tokens cannot be scoped to a single repo. A Pi compromise meant pushing to any public repo Mitch owns.
3. **It was readable by the wrong user.** Mode 640 `root:mitch` means `mitch` reads it — and agent-authored test code runs as `mitch` on this appliance (see [`racknerd-pi-access-spec.md`](racknerd-pi-access-spec.md)). The credential sat inside the blast radius of the thing it needed protecting from.

Removed rather than scoped down, on the principle applied throughout the Racknerd work: **capability absent beats capability forbidden.** A narrower token still has to be stored, rotated, and kept out of reach. No token has none of those problems.

Actions taken: token revoked in GitHub · `/etc/mpe/git-credentials` deleted · `credential.helper` unset local and global · push URL disabled · `setup-pi-github-pat.sh` changed to refuse to run.

---

## If a private repo is ever needed on the Pi

`MPE-Library` **is private**. As of 2026-08-16 `~/MPE-Library` exists on the appliance as a plain directory — **not a git checkout** — so nothing was broken by the credential removal. If it is ever cloned there:

**Use a read-only deploy key. Not a PAT.**

```bash
# On the Pi
ssh-keygen -t ed25519 -C "mpe-pi-readonly-$(date +%Y-%m)" -f ~/.ssh/mpe_pi_library_ro
cat ~/.ssh/mpe_pi_library_ro.pub
```

GitHub → **MPE-Library** → Settings → **Deploy keys** → Add deploy key. Paste the public key. **Leave "Allow write access" unchecked.** Date the title so age is visible without cross-referencing.

Then point that repo — and only that repo — at the SSH URL.

Why a deploy key rather than a fine-grained PAT:

| | Read-only deploy key | Fine-grained PAT (read-only) |
|---|---|---|
| Scope | One repository, by construction | Selected repos, by configuration |
| Ceiling | Clone/fetch that repo | Whatever the issuing account can reach, narrowed by scope |
| GitHub API | **None** | Yes, within scope — can enumerate repo, issues, Actions metadata |
| Revocation | Delete the key on the repo | Revoke the token on the account |

The deploy key's limits are structural; the PAT's are configured. Prefer the structural one.

Do **not** reuse it for `MPE-Sound-Module`, which needs no credential at all.

---

## Anti-patterns — do not reintroduce

- **A classic PAT anywhere on the appliance.** No per-repo scoping exists; `repo` and `public_repo` are both far wider than "pull one repo."
- **Any credential readable by `mitch`.** Agent code runs as that user. Until the `mpe-agent` split lands (see the spec), assume anything `mitch` can read is agent-reachable.
- **A credential for a public repo.** Anonymous HTTPS already works.
- **Blocking outbound SSH from the Pi as a substitute.** It breaks `git pull` in the deploy scripts and is not enforceable anyway — `mitch` has sudo and can flush any local firewall rule. Remove the credential instead; then there is nothing for an egress rule to protect.
