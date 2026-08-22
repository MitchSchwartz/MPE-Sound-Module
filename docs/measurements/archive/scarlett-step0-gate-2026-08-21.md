# Scarlett measurement — Step 0 config gate (2026-08-21)

*Last updated: 2026-08-21 (America/Toronto)*

Gate before Step 1–5 stream sampling. **Do not re-derive device facts below** — they
are the baseline for this session.

## Device record (given)

| Field | Value |
|---|---|
| ALSA card | **6** (`USB` / Scarlett 4i4 USB) |
| USB speed | **480M** (high speed) |
| USB path | hub **1-1.2** (`usb-0000:01:00.0-1.2`) |
| Transfer | **ASYNC** playback + **feedback** endpoint **0x81** |
| Format | **S32_LE** / 24-bit |
| Playback channels | **4** (FL FR FC LFE) |
| Packet size | **144** bytes (6 samples × 4 ch × 6 bytes per 125 µs microframe) |
| Sound Blaster | **unplugged** (Tier 1 out) |

## Topology (baseline — do not change mid-run)

```
usb1 (xhci 480M)
  └── 1-1 VIA hub
        ├── 1-1.1 LUMI Keys BLOCK   12M
        ├── 1-1.2 Scarlett 4i4      480M  ← jackd hw:6
        └── 1-1.3 APC MINI          12M
```

Hub runs transaction translation for the 12M devices.

## Gate checks

| Check | Result |
|---|---|
| Clock / sync | `Sync Status` = **Locked** (internal; no S/PDIF/ADAT control exposed in amixer) |
| Sample rate | **48000 Hz** running (`stream0` Momentary freq; JACK `rate=48000`) |
| Direct Monitor | No `Direct Monitor` control in ALSA mixer (Gen3 driver); analog path is PCM 1/2 → phones (set at session start) |
| `Standalone Switch` | **on** — noted; host playback verified working |
| JACK device | `hw:6` Scarlett |
| Peak meter under load | **PASS** — `peak_linear=0.255` with `midi-load.py 5` (3 voices) |
| `meter_live` / wired | `wired=1`, `jack_online=1` at gate time |

## Headphone routing (session setup)

Analogue Output 03/04 → PCM 1/2 (required for audible monitor; default was PCM 3/4).

## Pre-run jack state

```
period=512 periods=3 rate=48000
```

Harness will set **256×3** for Step 1.

## Verdict

**Gate passed.** Proceed to Step 1.
