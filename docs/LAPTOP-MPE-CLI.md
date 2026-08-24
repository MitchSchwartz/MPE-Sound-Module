# Laptop mpe-cli — multi-Pi setup

Per-host configs live outside the repo under `~/.config/mpe/`. The global `mpe` CLI reads `MPE_CLI_CONFIG` or a default file.

## Quick setup

```bash
mkdir -p ~/.config/mpe
cp config/laptop/mpe.env.pi4.example ~/.config/mpe/mpe.env.pi4
cp config/laptop/mpe.env.pi5.example ~/.config/mpe/mpe.env.pi5
# Edit PI_HOST, PI_USER, SSH_KEY for each board
```

## Usage

`capture-external-state.sh` and other provision scripts honor **`MPE_CLI_CONFIG`** (same as the `mpe` CLI). Set **`PI_MPE_MODULE`** to the repo path on the Pi (e.g. `/home/pi/MPE-Module`).

```bash
MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi4 mpe ping
MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 mpe status
```

Optional shell aliases (add to `~/.zshrc`):

```bash
alias mpe-pi4='MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi4 mpe'
alias mpe-pi5='MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 mpe'
```

## SSH Host blocks

Pin host keys and short names in `~/.ssh/config` (not captured by repo scripts):

```
Host pi4 pi4.local raspberrypi2.local
  HostName raspberrypi2.local
  User mitch
  IdentityFile ~/.ssh/id_ed25519_pi4

Host pi5 pi5.local raspberrypi5.local
  HostName raspberrypi5.local
  User mitch
  IdentityFile ~/.ssh/id_ed25519_pi5
```

## Backup

```bash
./scripts/provision/capture-laptop-mpe-config.sh
```

Writes `state/laptop-mpe-YYYY-MM-DD/` with `mpe.env.*`, pi4/pi5 **SSH Host blocks** (no private keys), and **mpe4/mpe5 shell aliases** when present.

## Appliance git refs

| Board | Ref file | Default branch |
|-------|----------|----------------|
| Pi 4 | `config/platform/appliance-git-ref.pi4` | `main` |
| Pi 5 | `config/platform/appliance-git-ref.pi5` | `dev` |

Override at build time: `./scripts/image/build-appliance.sh --git-ref …`

See also [`PI4-GOLDEN-IMAGE.md`](PI4-GOLDEN-IMAGE.md).
