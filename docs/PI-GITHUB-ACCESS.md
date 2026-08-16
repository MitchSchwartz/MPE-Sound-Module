# Pi GitHub access (classic PAT)

The Pi pulls **`MPE-Sound-Module`** (and **`MPE-Library`** when cloned) over **HTTPS** with a **classic personal access token** stored root-owned under `/etc/mpe/`. It does **not** use MitchSchwartz SSH keys or the **M-Ferda** machine account.

| Identity | Role |
|----------|------|
| **Laptop** | MitchSchwartz — SSH or gh, full owner |
| **Pi (`mitch`)** | Classic PAT — **pull only** in practice; stored in `/etc/mpe/git-credentials` |
| **Racknerd (`om-yolo`)** | Separate OneCLI `github-mpe-module` secret — not the Pi PAT |

## Create the classic token (GitHub UI)

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**.
2. **Note:** `mpe-pi-git-pull`
3. **Expiration:** pick one (90d / 1y — calendar reminder to rotate).
4. **Scopes:** **`repo`** (required for private `git pull` with classic tokens; there is no read-only classic scope for private repos).
5. Generate and copy the token once (`ghp_…`).

Do **not** paste the token in chat, argv, or git.

## Install on the Pi

From the laptop (token in env var, not history):

```bash
cd ~/Documents/GitHub/MPE-Module
printf '%s' "$GITHUB_TOKEN" | ssh mitch@raspberrypi2.local \
  'sudo bash -s -- --stdin' < scripts/setup-pi-github-pat.sh
```

Or on the Pi after pulling this script:

```bash
printf '%s' "$GITHUB_TOKEN" | sudo ./scripts/setup-pi-github-pat.sh --local --stdin
```

Verify:

```bash
ssh mitch@raspberrypi2.local \
  'GIT_TERMINAL_PROMPT=0 git -C ~/MPE-Module ls-remote origin HEAD'
```

## Retire M-Ferda (manual, after PAT works)

1. **MPE-Sound-Module** → Settings → Collaborators → remove **M-Ferda**.
2. **MPE-Library** → same (if that repo is still used on the Pi).
3. **M-Ferda** account → Settings → SSH keys → delete Pi device keys.
4. On Pi: remove `~/.ssh/config` `Host github.com` block if present (optional cleanup).

**Keep `om-yolo`** — Racknerd YOLO uses it via OneCLI; that path is unrelated to Pi `git pull`.

## Rotation

1. Generate a new classic token.
2. Re-run `setup-pi-github-pat.sh`.
3. Revoke the old token in GitHub.

## Related

- Maintainer history: OM-Repo `internal/projects/mpe-synth-launch/maintainer/PI-GITHUB-ISOLATION.md` (superseded by this doc for Pi auth).
- Racknerd deploy boundary: `docs/racknerd-pi-access-spec.md` — Pi `git checkout` only via future `mpe-yolo-remote.sh` deploy tokens + `pi_soak` gate.
