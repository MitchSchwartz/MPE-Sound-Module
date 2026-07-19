# Pi GitHub isolation (device account)

The Pi must **not** use your personal **MitchSchwartz** GitHub SSH key. That key can reach all of your work repos. Use a **device-only** identity instead.

## Target layout

| Machine | GitHub identity | Repo |
|---------|-----------------|------|
| **Laptop** | MitchSchwartz | [MitchSchwartz/MPE-Sound-Module](https://github.com/MitchSchwartz/MPE-Sound-Module) (canonical, public) |
| **Pi** | **M-Ferda** (device container account) | `M-Ferda/MPE-Sound-Module` (mirror — pull only) |
| **Laptop** | MitchSchwartz | `MitchSchwartz/MPE-Library` (private patches backup) |

The Pi never needs MitchSchwartz credentials.

## One-time setup (M-Ferda account)

Do these steps **logged into GitHub as M-Ferda** (browser), not MitchSchwartz.

### 1. Create the device mirror repo

On M-Ferda: **New repository** → `MPE-Sound-Module` (private is fine).

From your laptop, push a copy once:

```bash
cd MPE-Module   # local clone; folder name on disk can stay MPE-Module
git remote add mferda https://github.com/M-Ferda/MPE-Sound-Module.git   # if not exists
git push mferda main
```

(Or use GitHub **Import repository** from `MitchSchwartz/MPE-Sound-Module` while logged in as M-Ferda.)

### 2. Generate a Pi-only SSH key

On the Pi:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/mferda_github -C 'surge-pi-mferda' -N ''
```

Copy the public key:

```bash
cat ~/.ssh/mferda_github.pub
```

### 3. Add key to M-Ferda (not MitchSchwartz)

GitHub → **M-Ferda** → Settings → SSH and GPG keys → New SSH key → paste `mferda_github.pub`.

### 4. Configure Pi SSH to use only the device key

On the Pi, `~/.ssh/config`:

```
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/mferda_github
  IdentitiesOnly yes
```

Verify:

```bash
ssh -T git@github.com
# Must say: Hi M-Ferda! ...
```

### 5. Point the Pi clone at M-Ferda

```bash
cd ~/MPE-Module   # path on disk can stay; override with MPE_MODULE_REPO if you rename
git remote set-url origin git@github.com:M-Ferda/MPE-Sound-Module.git
git fetch origin
git status
```

### 6. Revoke Pi access to MitchSchwartz

On GitHub → **MitchSchwartz** → Settings → SSH keys: **delete** any key that was generated on the Pi (`id_ed25519` from `surge.local`). After this, even if the old key file remains on the Pi, it cannot reach your work account.

Optional on Pi: `mv ~/.ssh/id_ed25519 ~/.ssh/id_ed25519.disabled` so it cannot be picked up accidentally.

## Ongoing sync (laptop → Pi)

After you push to MitchSchwartz:

```bash
# laptop — update M-Ferda mirror
git push mferda main

# Pi — pull from device repo only
ssh surge.local 'cd ~/MPE-Module && git pull && bash scripts/configure-pi-paths.sh --local --force'
```

## Alternative: read-only deploy key (single repo, no M-Ferda mirror)

If you prefer one repo on MitchSchwartz only: add `mferda_github.pub` as a **read-only deploy key** on `MitchSchwartz/MPE-Sound-Module` (Repo → Settings → Deploy keys). The Pi can pull that repo only — still revoke the MitchSchwartz account key from the Pi as in step 6.

The M-Ferda mirror is cleaner if you want the device identity to match the container account end-to-end.
