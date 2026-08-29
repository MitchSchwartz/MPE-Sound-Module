# JACK driver fails to start at 64 x 2 on Sound Blaster Play! 3 — 2026-08-29

## Symptom

Stack came up dead on battery power. `jackd` was running as a process and held
`/dev/snd/pcmC1D0p`, but every client failed:

```
Driver is not running
Cannot create new client
BDB2034 unable to allocate memory for mutex; resize mutex region
Error: cannot connect to JACK, jack_client_open() failed, status = 0x21
```

`surge-xt-cli` restart-looped against the dead server. `mpe-restart-bench`
timed out and was killed.

## What it was NOT

Two hypotheses were tested and refuted before the real cause was found. Both
looked plausible and both were wrong — recorded so nobody re-runs them.

- **Not the DAC.** Direct ALSA playback succeeded:
  `aplay -D hw:1,0 -f S24_3LE -r 48000 -c 2 -d 3 /dev/zero` → exit 0.
- **Not CPU throttling.** At failure time: `arm clock = 2.4 GHz`, governor
  `performance`, `temp = 54.9 C`. Not clamped.
- **Not stale JACK shm.** Removing `/dev/shm/jack_db-1000`, `jack-1000-*`,
  `jack-shm-registry`, `jack_sem.*` and restarting reproduced the failure
  identically. The BDB errors are downstream noise from a driverless server,
  not a cause.

## What it was

`jackd` configured the ALSA driver, then the driver thread never signalled:

```
configuring for 48000Hz, period = 64 frames (1.3 ms), buffer = 2 periods
ALSA: use 2 periods for playback
JackPosixProcessSync::LockedTimedWait error usec = 5000000
Driver is not running
```

Buffer/period sweep on `hw:1` (Sound Blaster Play! 3), 48000 Hz, `-R -P70`,
pass = `system:playback_1` present in `jack_lsp` within 6 s:

| period | periods | result |
|--------|---------|--------|
| 64     | 2       | **FAIL** |
| 64     | 3       | OK |
| 128    | 2       | OK |
| 256    | 3       | OK |

**64 x 2 is the only failing combination**, and it was the configured value.
The Sound Blaster Play! 3 is a full-speed USB device; 2 x 64 frames is a
2.7 ms total buffer against 1 ms USB frames — too tight for the stream to
start.

## Fix

`/etc/mpe/mpe.env`: `MPE_JACK_PERIODS=2` -> `3`.
Backup at `/etc/mpe/mpe.env.bak-2026-08-29`.

Period size stays 64, so per-period latency is unchanged; total buffer goes
from ~2.7 ms to ~4 ms. Verified: full stack active, `Surge XT:out_*` connected
to `system:playback_*`, peak meter tapping.

## OPEN — retest on mains power

64 x 2 was in service before today, so it worked somewhere. Unresolved which:

1. **Was it ever good on the Sound Blaster?** Possible it has always been
   marginal on this DAC and only survived because nothing stressed stream
   start.
2. **Did it work only on the Scarlett 4i4?** The Scarlett is a high-speed
   device with a different frame budget and would tolerate 64 x 2 easily.

**Test when back on mains:** plug the Scarlett, set `MPE_JACK_PERIODS=2`,
restart `mpe-jackd`, check whether the driver starts. Then repeat on the
Sound Blaster to confirm the failure is device-specific and not a regression
introduced elsewhere.

This matters beyond this incident: if 64 x 2 is only safe on high-speed
interfaces, then **any future dongle DAC needs this sweep run before it is
trusted**, and the periods value should arguably be selected per-device
rather than pinned globally.

## Unrelated fault seen in the same window

Repeated brownouts on the 40,000 mAh / 3 A battery pack:

```
hwmon hwmon2: Undervoltage detected!   12:24:31, 12:27:23, 12:28:47, 12:32:03
vcgencmd get_throttled -> 0x50000      (bit 16 under-voltage occurred,
                                        bit 18 throttling occurred)
```

Also `usb 1-1: USB disconnect` (LUMI) at 12:34:26.

This did **not** cause the JACK failure — the sweep reproduces it with the
CPU unthrottled — but it is a real fault. Pi 5 needs 5 V/5 A PD (27 W); a 3 A
supply caps total USB peripheral current at 600 mA. Pass condition on a new
supply: `vcgencmd get_throttled` returns `0x0` after a full session.
