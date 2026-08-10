# MIDI clock — sync Boss RC-5 (and other gear)

**Default: looper as master** — the RC-5 sends MIDI clock over USB; the Pi listens and shows tempo in the touch header. **Optional: Pi as master** — send clock to the pedal when you want a fixed BPM before recording.

## Hardware

Plug the **Boss RC-5** into the Pi with a **USB Type-B cable** (same as a printer). No separate MIDI interface needed.

Use a **powered USB hub** if the Pi already hosts the Roli, audio dongle, and foot pedal.

### RC-5 settings

On the pedal: **MENU → SYNC → MIDI** or **AUTO** so it **sends** clock when playing/recording loops.

## Software

| Daemon | Role | Service |
|--------|------|---------|
| `midi-clock-in.py` | Listen to RC-5 clock (**default**) | `midi-clock-in.service` |
| `midi-clock-out.py` | Send clock from Pi (optional) | `midi-clock-out.service` |

Clock state is written to `~/.mpe_midi_clock_state.json`. The touch browser reads it for the **Looper tempo** badge in the header (● + BPM when the pedal is running).

### One-time install (Pi)

```bash
cd ~/MPE-Module
./scripts/install-midi-clock-service.sh
```

By default **`MPE_MIDI_CLOCK_IN_ENABLED=1`** — looper-as-master starts on boot. Override in `/etc/mpe/mpe.env` if needed.

### Config (`/etc/mpe/mpe.env`)

```bash
# Looper as master (recommended)
MPE_MIDI_CLOCK_IN_ENABLED=1
MPE_MIDI_CLOCK_IN_PORT=RC-5

# Pi as master (optional — disable IN or run both only if you know the routing)
# MPE_MIDI_CLOCK_OUT_ENABLED=1
# MPE_MIDI_CLOCK_BPM=120
```

### Smoke test

```bash
./scripts/midi-clock-in.py --list-ports
./scripts/midi-clock-in.py   # Ctrl+C to stop; watch RC-5 play → BPM in state file
cat ~/.mpe_midi_clock_state.json
```

Start/stop a loop on the RC-5 — header badge should show **● 120** (example) while clock is running.

### Touch UI

- Header badge: **LOOP** when idle, **120** when tempo known, **●** dot when transport is running
- **System settings → Advanced → Looper tempo** — hide/show the badge

## Sync direction (why looper-first)

For solo MPE + looper, most players either run **free time** or let the **first loop / tap tempo on the pedal** set the grid. The synth follows the looper, not the other way around.

Use **Pi as master** (`midi-clock-out`) when you want a **fixed BPM before** you hit record on the RC-5.

## Output timing (quantize + buffer offset)

`mpe-pressure-remap.py` can align Roli note-ons to the looper grid and advance them slightly so Surge hears them in time with the pedal.

| Variable | Default | Meaning |
|----------|---------|---------|
| `MPE_MIDI_OUTPUT_OFFSET_AUTO` | `1` | Derive offset from `MPE_SURGE_BUFFER_SIZE` / sample rate (negative ms) |
| `MPE_MIDI_OUTPUT_OFFSET_MS` | *(auto)* | Manual override; negative = advance |
| `MPE_MIDI_QUANTIZE` | `off` | `beat`, `8th`, `16th`, `32nd`, or `off` |
| `MPE_MIDI_CLOCK_THROUGH` | `1` | Forward realtime clock bytes to Surge |

Requires `midi-clock-in` running and RC-5 sending clock for quantize to engage. When unsynced, notes pass through immediately (offset still applies).

**Touch UI:** System settings → Advanced → **Looper sync** — quantize grid, auto buffer offset, and clock-through toggles. Changes persist in `/etc/mpe/mpe.env` and restart `mpe-pressure-remap.service`.

## Related

- [#40 MIDI thru/passthrough](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/40)
- [FOOT_PEDAL.md](FOOT_PEDAL.md)
