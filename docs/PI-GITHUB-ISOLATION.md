# Pi GitHub isolation

The Pi must **not** use your personal **MitchSchwartz account SSH key**. That key can reach all of your work repos.

**One repo only:** [MitchSchwartz/MPE-Sound-Module](https://github.com/MitchSchwartz/MPE-Sound-Module) — no duplicate under M-Ferda.

## How isolation works

| Machine | GitHub access | Scope |
|---------|---------------|--------|
| **Laptop** | MitchSchwartz (normal login / `gh`) | All your repos |
| **Pi** | **Read-only deploy key** (`~/.ssh/mpe_github`) | **This repo only** |

The Pi pulls from `git@github.com:MitchSchwartz/MPE-Sound-Module.git` but the key cannot see OM-Repo, MPE-Library, or anything else on your account.

## Pi setup (already scripted on device)

On the Pi:

- `~/.ssh/mpe_github` — device deploy key (not registered to your account)
- `~/.ssh/config` — forces `github.com` to use that key only (`IdentitiesOnly yes`)
- `~/MPE-Module` remote → `MitchSchwartz/MPE-Sound-Module`

Deploy key is added under **Repo → Settings → Deploy keys** on GitHub (title: `surge-pi-readonly`).

## Required: revoke the old Pi key from your account

If the Pi ever had `~/.ssh/id_ed25519` added to **MitchSchwartz → Settings → SSH keys**, **delete it there**. Until you do, that key still has full account access even if the Pi prefers the deploy key.

Optional on Pi after revoking:

```bash
mv ~/.ssh/id_ed25519 ~/.ssh/id_ed25519.disabled
mv ~/.ssh/id_ed25519.pub ~/.ssh/id_ed25519.pub.disabled
```

## Verify

```bash
ssh surge.local 'cd ~/MPE-Module && git fetch origin && git status'
```

Deploy keys do not respond to `ssh -T git@github.com` with “Hi username” — that is normal. `git fetch` is the test.

## Ongoing sync

After you push from the laptop:

```bash
ssh surge.local 'cd ~/MPE-Module && git pull'
```

No second repo or M-Ferda mirror needed.
