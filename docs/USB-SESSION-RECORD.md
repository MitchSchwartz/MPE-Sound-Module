# USB session record (`usb-host-session` profile)

Record the **full looping session** (Surge → RC-5 → return) on a tethered PC over **one USB cable** — without moving Surge to the gadget.

## Signal chain

```
Seaboard → Pi (Surge) → Sound Blaster OUT ──→ RC-5 IN
                                    ↑              │
                                    │         [loops]
                                    │              ↓
                              headphones    RC-5 OUT
                                    │              ↓
                                    │      Sound Blaster MIC IN
                                    │              ↓
                                    │      mic → UAC2 bridge
                                    │              ↓
                                    └──────  PC records USB input
```

- **Surge** always plays to **Sound Blaster** (headphones + RC-5 feed).
- **PC capture** receives **mic in** (pedal return), not direct Surge.
- Headphones hear **dry Surge** (same as RC-5 input). Loops are on the **recording**, not in the cans unless you monitor in the DAW.

## Enable (Pi)

1. One-time: same USB gadget boot setup as [`USB-AUDIO-HOST.md`](USB-AUDIO-HOST.md) (`dtoverlay=dwc2`, etc.).

2. `/etc/mpe/mpe.env`:

   ```bash
   MPE_AUDIO_PROFILE=usb-host-session
   ```

3. Refresh units and reboot (or):

   ```bash
   ./scripts/configure-pi-paths.sh --local --force
   sudo systemctl restart usb-audio-gadget uac2-stall-watchdog surge-xt-cli
   ```

4. Wire **RC-5 OUT → Sound Blaster mic in** (pad level — mic jack, not line in).

5. Split **Sound Blaster out** → headphones + RC-5 IN.

## On the PC

Same as `usb-host`: arm a track on **USB Audio Passthrough** / **MPE Sound Module** @ 48 kHz stereo. The host-route watcher starts `mic-to-uac2-bridge` when capture opens.

```bash
arecord -D hw:N,0 -f S16_LE -r 48000 -c 2 -d 10 /tmp/session.wav
```

## vs `usb-host`

| Profile | Surge output when PC records | PC hears |
|---------|------------------------------|----------|
| `usb-host` | UAC2 gadget (direct) | Synth only |
| `usb-host-session` | Sound Blaster (always) | RC-5 loop mix |

## Level / quality notes

- Mic in is **mono**; bridge duplicates to stereo for UAC2.
- Keep RC-5 **LOOP LEVEL** conservative to avoid clipping mic in.
- Extra A/D latency on the return path — fine for recording, not for loop monitoring in headphones.
