# Shutdown stop-timeout fix (2026-08-21)

*No Pi measurement required — deploy + one labeled shutdown test.*

## Symptom

Poweroff/reboot felt stuck ~1 minute. Journal showed units sitting at their stop timeout:

| unit | stop timeout |
|---|---|
| `mpe-peak-meter` | **90s** (inherited default — missed in prior pass) |
| `user@1000` | 120s |
| tailscaled, NetworkManager, wpa_supplicant, avahi, bluetooth | 90s each |

MPE audio units were already bounded (jackd 10s, surge 15s, touch 10s, midi-clock 5s). **`DefaultTimeoutStopSec=90s`** globally is the rest.

## Fix (repo)

1. **`config/systemd/mpe-appliance.conf`** — `[Manager] DefaultTimeoutStopSec=10s`
   - Installed by `apply-appliance-hygiene.sh` → `/etc/systemd/system.conf.d/mpe-appliance.conf`
   - `systemctl daemon-reexec` when the file changes
2. **`config/mpe-peak-meter.service`** — `TimeoutStopSec=5`, `KillMode=mixed`
3. **`native/mpe-peak-meter/mpe-peak-meter.c`** — 100 ms interruptible waits so SIGTERM / `jack_on_shutdown` is not delayed by `sleep(1)` or 2 s connect polling

Per-unit overrides unchanged (`mpe-shutdown-splash` = infinity).

## Deploy

```bash
git pull   # on dev after merge
sudo ./scripts/configure-pi-paths.sh --local --force
# rebuild peak meter if binary changed:
cd native/mpe-peak-meter && make && sudo cp mpe-peak-meter /usr/local/bin/
sudo systemctl daemon-reload
```

## Verify (optional, ~2 min)

```bash
./scripts/shutdown-mark-test.sh ssh "post-timeout-fix"
sudo systemctl poweroff
# after boot:
./scripts/shutdown-measure-last.sh
```

Expect `mpe-peak-meter` stop under 5s and no unit gap at 90s unless something else is still unpinned.

*Last updated: 2026-08-21 (America/Toronto)*
