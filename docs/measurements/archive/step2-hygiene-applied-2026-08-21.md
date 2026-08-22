# Step 2 — Phase 0 hygiene applied (2026-08-21)

**Pi:** raspberrypi2 · **Branch:** `yolo/system-hygiene-baseline` @ `11789d6`  
**Reboot:** yes (cmdline `video=HDMI-A-1:d video=HDMI-A-2:d`)

## Code + units deployed

| item | change |
|---|---|
| peak meter C | exit 0 on jack shutdown (no false failure restart) |
| peak meter unit | `CPUAffinity=2 3`, `PartOf=mpe-jackd` |
| harness | VOID window if meter xruns go backwards; probe `taskset -c 2-3` |
| `MeterXrunCounter` | `None` on mid-run restart |
| pressure remap | wait USB **+** ALSA MIDI; `Restart=no`; start wrapper idle exit |
| IRQ | `mpe-irq-affinity.service` → IRQ 41/42/43/28/57 on CPU1 |
| cmdline | `boot-assert-cmdline.sh` on jackd prestart; HDMI disable tokens added |
| hygiene script | timers masked, cloud-init/cron/avahi/bluetooth/udisks pruned, WiFi PS off |

## Post-reboot verification

| check | result |
|---|---|
| `jack_bufsize` | 1024 |
| `irqaffinity=0,1` | present |
| HDMI disable cmdline | present |
| movable IRQs → CPU1 | applied at boot |
| `meter_live` path | `MPE_PEAK_METER=1`, service active |
| pressure remap | inactive when LUMI unplugged (idle exit 0 — correct) |

## Display (2e)

- **Kept:** `vc4-kms-dsi-7inch`, DSI-1 connected, pygame touch UI
- **Applied:** kernel disable of disconnected HDMI-A-1/A-2 (not vc4 removal)
- **Deferred:** v3d blacklist — pygame does not use GL; IRQ 43 still present

## Not replicated / open

- `usb-audio-gadget` still enumerates (card 5); profile is standalone — disabled in hygiene run but may re-enable on configure-pi-paths if gadget persist flag set. Re-check before quoting USB-host numbers.

*Measured on device 2026-08-21 after reboot.*
