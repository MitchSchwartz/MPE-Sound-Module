# Command Reference

Quick reference for all backup, deployment, and device management commands.

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
ssh surge.local 'systemctl status surge-xt-cli'
ssh surge.local 'systemctl status patch-browser'
ssh surge.local 'systemctl status boot-animation'
```

Or check all services:

```bash
ssh surge.local 'systemctl status surge-xt-cli patch-browser boot-animation'
```

---

### View Logs

**Surge logs:**
```bash
ssh surge.local 'tail -30 ~/surge-cli.log'         # Last 30 lines
ssh surge.local 'tail -f ~/surge-cli.log'          # Follow in real-time
```

**System logs:**
```bash
ssh surge.local 'sudo journalctl -u surge-xt-cli -n 50'
ssh surge.local 'sudo journalctl -u patch-browser -n 50'
```

---

### Restart Services

**Restart Surge:**
```bash
ssh surge.local 'sudo systemctl restart surge-xt-cli'
```

**Restart all services:**
```bash
ssh surge.local 'sudo systemctl restart surge-xt-cli patch-browser boot-animation'
```

---

### Clear Logs

```bash
ssh surge.local 'echo "" > ~/surge-cli.log'
```

---

## SSH Commands

### Connect to Pi

```bash
ssh surge.local
# or
ssh mitch@surge.local
# or
ssh mitch@192.168.1.203
```

With SSH key:
```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local
```

---

### Copy Files To Pi

```bash
scp -i ~/.ssh/surge_pi_key localfile.txt mitch@surge.local:~/
```

---

### Copy Files From Pi

```bash
scp -i ~/.ssh/surge_pi_key mitch@surge.local:~/remotefile.txt ./
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
export PI_HOST=surge.local        # Pi hostname (default: surge.local)
export PI_USER=mitch              # SSH user (default: mitch)
export SSH_KEY=~/.ssh/surge_pi_key  # SSH key path
```

Use in commands:
```bash
PI_HOST=192.168.1.203 bash scripts/deploy-all.sh
```

---

## Troubleshooting Commands

### Test SSH Connection

```bash
ssh -i ~/.ssh/surge_pi_key mitch@surge.local "echo 'Connected'"
```

---

### Check SSH Key Permissions

```bash
ls -la ~/.ssh/surge_pi_key
chmod 600 ~/.ssh/surge_pi_key
```

---

### Verify Assets Downloaded

From MPE-Module (reads sibling MPE-Personal):

```bash
ls -lh ../MPE-Personal/assets/binaries/surge-xt-cli
du -sh ../MPE-Personal/assets/patches/*
find ../MPE-Personal/assets -type f | wc -l
```

---

### Check Disk Space on Pi

```bash
ssh surge.local 'df -h'
```

---

### Check Process Running

```bash
ssh surge.local 'ps aux | grep surge'
```

---

### Kill Hung Process

```bash
ssh surge.local 'sudo systemctl stop surge-xt-cli'
ssh surge.local 'killall surge-xt-cli'
```

---

## Quick Reference Table

| Task | Command |
|------|---------|
| **Initial backup** | `bash scripts/pull-all-from-device.sh` |
| **Weekly sync** | `bash scripts/sync-from-device.sh` |
| **Deploy to Pi** | `bash scripts/deploy-all.sh` |
| **Check status** | `ssh surge.local 'systemctl status surge-xt-cli'` |
| **View logs** | `ssh surge.local 'tail -30 ~/surge-cli.log'` |
| **Restart Surge** | `ssh surge.local 'sudo systemctl restart surge-xt-cli'` |
| **Connect to Pi** | `ssh surge.local` |
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
ssh surge.local 'systemctl status surge-xt-cli'
ssh surge.local 'tail -30 ~/surge-cli.log'
```

**Time:** 10-15 minutes

---

### Update Script on Pi

```bash
# 1. Edit script locally
# 2. Deploy single script
scp -i ~/.ssh/surge_pi_key scripts/start-surge-cli.sh mitch@surge.local:~/

# 3. Make executable
ssh surge.local 'chmod +x ~/start-surge-cli.sh'

# 4. Restart service
ssh surge.local 'sudo systemctl restart surge-xt-cli'

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
scp -i ~/.ssh/surge_pi_key config/surge-xt-cli.service mitch@surge.local:~/
ssh surge.local 'sudo cp ~/surge-xt-cli.service /etc/systemd/system/'
ssh surge.local 'sudo systemctl daemon-reload'
ssh surge.local 'sudo systemctl restart surge-xt-cli'

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
alias pi='ssh surge.local'
alias pilogs='ssh surge.local "tail -f ~/surge-cli.log"'
alias pistatus='ssh surge.local "systemctl status surge-xt-cli"'
alias pirestart='ssh surge.local "sudo systemctl restart surge-xt-cli"'
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

See [docs/BACKUP_GUIDE.md](docs/BACKUP_GUIDE.md) for detailed backup procedures and troubleshooting.
