# Command Reference

Quick reference for backup, deployment, and device management. **Set `PI_HOST`, `PI_USER`, and `SSH_KEY` in `config/mpe.env`** (copy from `config/mpe.env.example`) — examples below use those variables.

```bash
# Optional: load env before running commands
set -a && source config/mpe.env && set +a
```

---

## Backup Commands

### Initial Backup (One Time)

Pull everything from device to git repo:

```bash
bash scripts/pull-all-from-device.sh
```

**What it does:**
- Downloads Surge binary (24MB)
- Downloads all patches (422MB: factory + third-party)
- Downloads system configs (systemd services, udev rules)
- Downloads user data (preferences, custom patches)

**Time:** 5-10 minutes

---

### Ongoing Sync (Weekly)

Sync only changed files from device:

```bash
bash scripts/sync-from-device.sh
```

**What it does:**
- Syncs modified system configs
- Syncs user preferences
- Syncs custom patches (if any)
- Does NOT re-download binary or factory patches

**Time:** 10-30 seconds

---

### Commit and Push

After running sync, commit changes to git:

```bash
git status                                   # Review changes
git add -A                                   # Stage all changes
git commit -m "Backup $(date +%Y-%m-%d)"    # Commit with date
git push                                     # Push to GitHub
```

**First push:** 5-10 minutes (~450MB)
**Subsequent pushes:** 3-5 seconds (only changed files)

---

## Deployment Commands

### Full Deployment

Deploy everything from git to Pi:

```bash
bash scripts/deploy-all.sh
```

**What it does:**
1. Creates directories on Pi
2. Deploys Surge binary
3. Deploys all patches (factory + third-party)
4. Deploys scripts
5. Deploys Python scripts
6. Deploys systemd services
7. Deploys udev rules
8. Starts services

**Time:** 5-10 minutes

**Use when:**
- Fresh SD card (disaster recovery)
- New Pi device
- Complete system restore

---

### Deploy to Different Pi

Use environment variables to deploy to a specific Pi:

```bash
export PI_HOST=192.168.1.203
bash scripts/deploy-all.sh
```

Or:

```bash
PI_HOST=other-pi.local bash scripts/deploy-all.sh
```

---

## Device Management Commands

### Check Service Status

```bash
ssh $PI_USER@$PI_HOST 'systemctl status surge-xt-cli'
ssh $PI_USER@$PI_HOST 'systemctl status patch-browser'
ssh $PI_USER@$PI_HOST 'systemctl status boot-animation'
```

Or check all services:

```bash
ssh $PI_USER@$PI_HOST 'systemctl status surge-xt-cli patch-browser boot-animation'
```

---

### View Logs

**Surge logs:**
```bash
ssh $PI_USER@$PI_HOST 'tail -30 ~/surge-cli.log'         # Last 30 lines
ssh $PI_USER@$PI_HOST 'tail -f ~/surge-cli.log'          # Follow in real-time
```

**System logs:**
```bash
ssh $PI_USER@$PI_HOST 'sudo journalctl -u surge-xt-cli -n 50'
ssh $PI_USER@$PI_HOST 'sudo journalctl -u patch-browser -n 50'
```

**OSC ports (localhost only):**

Surge XT CLI uses two UDP ports on `127.0.0.1`. They are easy to confuse when debugging “Surge looks dead” or patch loads fail silently.

| Port | Direction | Used for |
|------|-----------|----------|
| **53280** | In (Surge listens) | Commands from the patch browser — `/patch/load`, `/param/...` volume, Hold, etc. `SurgeMonitor` health checks whether this port is bound. |
| **53270** | Out (Surge replies) | Query replies — `PatchLoader` sends `/q/param/...` here and binds locally to read float responses (Hold baseline capture after patch load). |

Quick checks on the Pi:

```bash
ss -ulnp | grep -E '53270|53280'
pgrep -af surge-xt-cli
tail -30 ~/surge-cli.log
```

If **53280** is not listening, Surge is not running or failed during audio/MIDI startup. If **53280** is up but Hold or patch queries fail, confirm `start-surge-cli.sh` passes `--osc-out-port=53270` and that nothing else bound 53270.

---

### Restart Services

**Restart Surge:**
```bash
ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli'
```

**Restart all services:**
```bash
ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli patch-browser boot-animation'
```

---

### Clear Logs

```bash
ssh $PI_USER@$PI_HOST 'echo "" > ~/surge-cli.log'
```

---

## SSH Commands

### Connect to Pi

```bash
ssh $PI_USER@$PI_HOST
# or
ssh <pi-user>@<hostname>
# or
ssh <pi-user>@192.168.1.203
```

With SSH key:
```bash
ssh -i "$SSH_KEY" $PI_USER@$PI_HOST
```

---

### Copy Files To Pi

```bash
scp -i "$SSH_KEY" localfile.txt $PI_USER@$PI_HOST:~/
```

---

### Copy Files From Pi

```bash
scp -i "$SSH_KEY" $PI_USER@$PI_HOST:~/remotefile.txt ./
```

---

## Git Commands

### Clone Repository

```bash
git clone https://github.com/yourusername/MPE-Module.git
cd MPE-Module
```

---

### Check Status

```bash
git status           # See what changed
git diff             # See detailed changes
git log --oneline    # See recent commits
```

---

### Undo Changes

**Undo uncommitted changes:**
```bash
git checkout -- filename.txt    # Undo specific file
git reset --hard                # Undo all changes
```

**Undo last commit (keep changes):**
```bash
git reset --soft HEAD~1
```

---

## Environment Variables

Override default settings:

```bash
export PI_HOST=your-pi.local      # Pi hostname
export PI_USER=your-pi-username   # SSH user (required — set in mpe.env)
export SSH_KEY=$HOME/.ssh/your_pi_key
```

Use in commands:
```bash
PI_HOST=192.168.1.203 bash scripts/deploy-all.sh
```

---

## Troubleshooting Commands

### Test SSH Connection

```bash
ssh -i "$SSH_KEY" $PI_USER@$PI_HOST "echo 'Connected'"
```

---

### Check SSH Key Permissions

```bash
ls -la "$SSH_KEY"
chmod 600 "$SSH_KEY"
```

---

### Verify Assets Downloaded

From MPE-Module (reads sibling assets repo via `MPE_PERSONAL_REPO`):

```bash
ls -lh ../mpe-assets/assets/binaries/surge-xt-cli
du -sh ../mpe-assets/assets/patches/*
find ../mpe-assets/assets -type f | wc -l
```

---

### Check Disk Space on Pi

```bash
ssh $PI_USER@$PI_HOST 'df -h'
```

---

### Check Process Running

```bash
ssh $PI_USER@$PI_HOST 'ps aux | grep surge'
```

---

### Kill Hung Process

```bash
ssh $PI_USER@$PI_HOST 'sudo systemctl stop surge-xt-cli'
ssh $PI_USER@$PI_HOST 'killall surge-xt-cli'
```

---

## Quick Reference Table

| Task | Command |
|------|---------|
| **Initial backup** | `bash scripts/pull-all-from-device.sh` |
| **Weekly sync** | `bash scripts/sync-from-device.sh` |
| **Deploy to Pi** | `bash scripts/deploy-all.sh` |
| **Check status** | `ssh $PI_USER@$PI_HOST 'systemctl status surge-xt-cli'` |
| **View logs** | `ssh $PI_USER@$PI_HOST 'tail -30 ~/surge-cli.log'` |
| **Restart Surge** | `ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli'` |
| **Connect to Pi** | `ssh $PI_USER@$PI_HOST` |
| **Git status** | `git status` |
| **Commit** | `git add -A && git commit -m "message"` |
| **Push** | `git push` |

---

## Complete Workflow Examples

### Weekly Backup Routine

```bash
# 1. Sync from device
bash scripts/sync-from-device.sh

# 2. Review changes
git status
git diff

# 3. Commit and push
git add -A
git commit -m "Weekly backup $(date +%Y-%m-%d)"
git push
```

**Time:** 1-2 minutes

---

### Disaster Recovery

```bash
# 1. Clone repository
git clone https://github.com/yourusername/MPE-Module.git
cd MPE-Module

# 2. Deploy everything
bash scripts/deploy-all.sh

# 3. Verify
ssh $PI_USER@$PI_HOST 'systemctl status surge-xt-cli'
ssh $PI_USER@$PI_HOST 'tail -30 ~/surge-cli.log'
```

**Time:** 10-15 minutes

---

### Update Script on Pi

```bash
# 1. Edit script locally
# 2. Deploy single script
scp -i "$SSH_KEY" scripts/start-surge-cli.sh $PI_USER@$PI_HOST:~/

# 3. Make executable
ssh $PI_USER@$PI_HOST 'chmod +x ~/start-surge-cli.sh'

# 4. Restart service
ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli'

# 5. Sync back to git
bash scripts/sync-from-device.sh
git add -A && git commit -m "Update start script" && git push
```

---

### Modify Config and Deploy

```bash
# 1. Edit config locally
nano config/surge-xt-cli.service

# 2. Deploy config
scp -i "$SSH_KEY" config/surge-xt-cli.service $PI_USER@$PI_HOST:~/
ssh $PI_USER@$PI_HOST 'sudo cp ~/surge-xt-cli.service /etc/systemd/system/'
ssh $PI_USER@$PI_HOST 'sudo systemctl daemon-reload'
ssh $PI_USER@$PI_HOST 'sudo systemctl restart surge-xt-cli'

# 3. Commit to git
git add config/surge-xt-cli.service
git commit -m "Update surge service config"
git push
```

---

## Tips

**Use aliases for common commands:**

Add to `~/.bashrc` or `~/.bash_profile`:

```bash
alias pi='ssh $PI_USER@$PI_HOST'
alias pilogs='ssh $PI_USER@$PI_HOST "tail -f ~/surge-cli.log"'
alias pistatus='ssh $PI_USER@$PI_HOST "systemctl status surge-xt-cli"'
alias pirestart='ssh $PI_USER@$PI_HOST "sudo systemctl restart surge-xt-cli"'
alias backup='bash scripts/sync-from-device.sh'
```

Then use:
```bash
pi              # Connect to Pi
pilogs          # Follow logs
pistatus        # Check status
pirestart       # Restart Surge
backup          # Sync from device
```

---

## Demo capture (touch UI)

From your laptop — install **mpe-cli** (`./install.sh`), config in `~/.config/mpe/mpe.env`:

```bash
mpe ping
mpe status
mpe logs touch -n 80
mpe osc-check
mpe diagnose
mpe sysinfo                # board, kernel, governor, RT limits, block latency, throttle + real clock
mpe power 20               # sample ARM clock/throttle/volts for 20s — play during the window
mpe rt status              # configured vs live SCHED_FIFO for Surge and looper
mpe rt surge 20            # set realtime priority (1-95 or off); restarts that service
mpe restart surge          # or touch | all

mpe record                  # SSH to Pi, record until Ctrl+C
mpe record ~/mpe-demo.mkv 15
mpe pull-videos               # → ./recordings/
mpe pull-videos -o ~/Videos --delete-source
```

On the Pi directly: `./scripts/record-screen.sh` (see [docs/TOUCH_PATCH_BROWSER.md](docs/TOUCH_PATCH_BROWSER.md)).

Allowlist / pattern: [OM-Repo appliance-cli-pattern](https://github.com/opsMachine/OM-Repo/blob/main/Docs/appliance-cli-pattern.md) · [MPE-Module AGENTS.md](AGENTS.md) § Pi CLI.

---

See [docs/BACKUP_GUIDE.md](docs/BACKUP_GUIDE.md) for detailed backup procedures and troubleshooting.
