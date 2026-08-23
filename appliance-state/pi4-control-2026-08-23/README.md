# Pi 4 control condition — 2026-08-23

Captured after B2 Gate 1 soak PASS (A5 closeout). Companion to calibration refresh via `scripts/backup-appliance-state.sh`.

| Field | Value |
|-------|-------|
| MPE-Module | `9060236` |
| Surge revision | `253f8d86` (stock control; a72 null) |
| Kernel | 6.18.34+rpt-rpi-v8 |
| JACK | jackdmp 1.9.22 |
| Buffer (soak) | 1024×2 instrument profile |
| Core affinity | jackd/surge/looper `CPUAffinity=2 3`; `irqaffinity=0,1` |
| Clock | 1800 MHz configured (`arm_boost=1`) |
| Throttle | `0x0` at capture |

Full dump: [`appliance-snapshot.txt`](appliance-snapshot.txt) (includes `/boot/firmware/config.txt`).
