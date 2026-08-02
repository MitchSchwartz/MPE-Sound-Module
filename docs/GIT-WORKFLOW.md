# Git workflow — branches, Pi testing, promotion to main

*Last updated: 2026-08-02 (America/Toronto)*

This repo uses a **three-layer** flow: feature work → **`dev`** (integration + Pi soak) → **`main`** (release line on the device when stable).

**Do not push to `dev` and `main` in the same testing pass.** That skips the integration step and makes it unclear what the Pi is actually running.

---

## Branches

| Branch | Role |
|--------|------|
| `yolo/*` or other feature branches | Atomic work — one task or fix per branch |
| **`dev`** | Integration branch — merge feature work here; **test on the Pi from `dev`** |
| **`main`** | Release line — promote from `dev` only after confident soak on the Pi |

`main` is what the Pi should track **when everything is stable and gig-ready**. It is not the place to land work that is still being validated.

---

## Where to develop

Choose based on scope:

| Situation | Start here |
|-----------|------------|
| Focused task (bugfix, one feature, agent YOLO pass) | Feature branch → PR/merge to **`dev`** |
| Small doc-only or trivial fix Mitch is doing inline | Commit directly on **`dev`** is OK |
| Release promotion | Merge **`dev` → `main`** after Pi validation |

Feature branches merge into **`dev`**, not straight into **`main`**, unless it is an emergency hotfix Mitch explicitly routes that way.

---

## Pi testing — switch the clone, do not double-promote

The reference Pi (`~/MPE-Module`) is a normal git clone. **Check out the branch you are testing** instead of merging untested work to `main` on GitHub.

### Test a feature branch

```bash
# on Pi (SSH)
cd ~/MPE-Module
git fetch origin
git checkout yolo/your-branch
git pull origin yolo/your-branch
./scripts/configure-pi-paths.sh --local --force
# restart services if needed — see configure output
```

### Test integration (`dev`)

```bash
cd ~/MPE-Module
git fetch origin
git checkout dev
git pull origin dev
./scripts/configure-pi-paths.sh --local --force
```

### Return to stable (release line)

After soak passes and `dev` has been promoted to `main`:

```bash
cd ~/MPE-Module
git fetch origin
git checkout main
git pull origin main
./scripts/configure-pi-paths.sh --local --force
```

**Which branch is checked out on the Pi is the source of truth for what is deployed.** `git branch --show-current` before debugging.

Appliance settings (`MPE_AUDIO_PROFILE`, `MPE_UI_MODE`, buffer size, etc.) live in **`/etc/mpe/mpe.env`** and survive branch switches and `configure-pi-paths.sh --force` — they are not reset by checking out a different git branch.

---

## Promotion to `main` (human gate)

1. Feature branch merges to **`dev`** (PR or fast-forward).
2. Pi checks out **`dev`**, runs `configure-pi-paths.sh --local --force`, soaks the change (boot, audio, UI, USB profile as relevant).
3. When confident: merge **`dev` → `main`** (PR preferred).
4. Pi checks out **`main`** and pulls — back on the release line.

Until step 3 completes, **`main` should not receive the change**.

---

## Agent / automation rules

These apply to Cursor agents, YOLO headless runs, and anyone deploying from a laptop:

| Do | Don't |
|----|-------|
| Open PRs **feature → `dev`** | Merge the same change to **`dev` and `main` in one session** while still testing |
| Deploy to Pi by **checking out the branch under test** | Assume `git pull` on `main` is how you test new work |
| Merge **`dev` → `main`** only after Mitch confirms Pi soak (or explicit "promote to main") | Auto-merge to `main` because tests pass locally |
| Leave Pi on **`main`** when handing back a stable appliance | Leave Pi on a **`yolo/*`** branch after promotion without switching back |

From the laptop, remote configure still works — pass the branch explicitly:

```bash
# from PC — test dev on Pi without merging to main
ssh mitch@<pi-host> 'cd ~/MPE-Module && git fetch origin && git checkout dev && git pull origin dev && ./scripts/configure-pi-paths.sh --local --force'
```

Deploy scripts that run `git pull` without a branch name follow **whatever branch is currently checked out** on the Pi. Prefer explicit checkout in agent runbooks.

---

## Quick reference

```text
feature/yolo/*  ──merge──►  dev  ──soak on Pi (checkout dev)──►  main  ──Pi checkout main──►  stable appliance
```

**Stable Pi:** `git branch --show-current` → `main`  
**Integration test:** checkout `dev`  
**Feature test:** checkout `yolo/*` or the PR head branch  

See also: [PATHS.md](PATHS.md) · [STABLE-SETUP.md](STABLE-SETUP.md) · [PI-BOOT-RECOVERY.md](PI-BOOT-RECOVERY.md)
