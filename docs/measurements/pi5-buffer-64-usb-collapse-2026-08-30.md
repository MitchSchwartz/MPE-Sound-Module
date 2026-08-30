# Pi 5 JACK buffer 64 — the ALSA/USB collapse, diagnosed (2026-08-30)

*Last updated: 2026-08-30 (America/Toronto)*

**Status:** **Measured**, except for one inferred causal link, flagged below.
Closes the "for reasons never diagnosed" note in
[`player-env-parity.pi5.env`](../../config/platform/player-env-parity.pi5.env)
and supplies the two top-priority gaps listed in
[`pi5-buffer-64-ear-2026-08-27.md`](pi5-buffer-64-ear-2026-08-27.md) —
*xrun count over a defined window* and *voice count at which it degrades*.

---

## What happened

The operator's ROLI **LUMI Keys BLOCK** left the USB bus mid-session and would
not come back. It was first reported as "I changed audio output and that killed
the ROLI connection."

This is the **second** occurrence. The parity file already recorded that
"64x2 collapsed ALSA/USB on 2026-08-23 for reasons never diagnosed." The
2026-08-23 event has no logs; this one does.

## Measured

**Configuration actually running** (not what the parity file intends):

| | Parity file | Live on the unit |
|---|---|---|
| `MPE_JACK_BUFFER` | 64 | 64 |
| `MPE_JACK_PERIODS` | 2 | **3** |
| `MPE_SURGE_BUFFER_SIZE` | 128 | **1024** |

`jackd -R -P70 -d alsa -P hw:4 -r 48000 -p 64 -n 3` — a **1.33 ms** period at
realtime priority 70. The divergences are `apply-player-env-parity.sh`'s
`PRESERVE_KEY_RE` doing its job: a live value already present is kept, so the
unit drifts from the file that supposedly describes it.

**xrun count over a defined window** — the gap the ear-only doc named first:

| Hour | xruns |
|---|---|
| 08:00–12:59 | **0** |
| 13:00 | **11** |
| 14:00 | **24** |

**Voice count at which it degrades** — the second gap. The poly-governor
climbed 28 → 43 voices, `reason=recover`, `cpu=100.0` sustained, on patch
**"70s Fizzy String"**. JACK reported `Surge XT was not finished` and
`mpe-looper was not finished` continuously through the window.

**USB events**, all inside the xrun window:

```
14:19:42  set-audio-profile.sh standalone   (enables usb-audio-gadget, restarts graph)
14:20:01  usb 3-1.3 LUMI Keys BLOCK: USB disconnect
14:20:57  usb 3-1.3: reconnect
14:21:24  usb 3-1.3: disconnect            (27 s alive)
14:23:55  usb 3-1.3: reconnect
14:24:06  usb 3-1.2 APC mini mk2: urb status -32 storm, disconnect + re-enumerate
14:24:56  usb 3-1.3: disconnect            (61 s alive; did not return)
```

Both devices are on **one interrupt line**:

```
137:  159872486  ...  rp1_irq_chip  36 Edge  xhci-hcd:usb3
```

`3-1.2` is the APC, `3-1.3` is the LUMI. That IRQ thread had accumulated
**89 minutes** of CPU time.

## Excluded by measurement

**Power is not involved.** This was the first hypothesis and it was wrong.

- The dock's hub reports `bMaxPower=0mA`, `bmAttributes=e0` — bit 6 set,
  **self-powered**. The Pi's USB current budget does not feed these devices.
- `vcgencmd get_throttled` = `0x0`; `EXT5V_V` = 5.06 V.
- No `over-current` or `power budget` message anywhere in the kernel log.

A power-budget explanation also cannot account for the unit running **13 hours
without a fault** on unchanged hardware before the first drop.

**The audio-profile switch did not change the buffer.** `set-audio-profile.sh`
writes `MPE_AUDIO_PROFILE`, enables the gadget services and restarts the graph.
It never touches `MPE_JACK_BUFFER`. The 14:20:02 mtime on `/etc/mpe/mpe.env` is
the script rewriting the whole file; 64 was already there, and had been since
`5aba06a` on 2026-08-27.

## The one inferred link

Missed audio deadlines → USB device drop is **reasoned, not demonstrated.**

The proposed mechanism: at a 1.33 ms period with jackd at RT priority 70 and
the DSP graph unable to make the deadline, the `xhci-hcd:usb3` interrupt thread
is starved; URBs time out; the controller reports `-32` (EPIPE) and drops the
device. It accounts for every fact above — the sudden onset after 13 clean
hours, the load dependence, and why *two* devices on the same controller failed
together while nothing on the other controller did.

It is recorded as the leading hypothesis, not as established. The
discriminating test is below.

## The test that would settle it

1. Set `MPE_JACK_BUFFER=128` — the fallback the parity file already prescribes.
2. Replug the LUMI (a dropped device does not return in software).
3. Load "70s Fizzy String" and play to the polyphony that pegged the governor.
4. Count xruns over a defined window and watch for `urb status -32`.

If the drops stop, the mechanism is confirmed. If they continue at 128 with
xruns at zero, the mechanism is wrong and the cause is elsewhere.

## Recommendation

`MPE_JACK_BUFFER=64` is **ear-validated only and has now failed twice under
load.** The parity file's own prescription — *"if a unit misbehaves, fall back
to 128"* — has been triggered, and this is the second trigger.

Against that: 64×3 is 4 ms of JACK buffering and 128×3 is 8 ms, on an
instrument whose whole point is touch response. **That trade belongs to the
operator**, who chose 64 by ear and is the only one who can hear what it costs.
Nothing here should be changed on his behalf without him saying so.
