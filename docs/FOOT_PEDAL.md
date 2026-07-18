# USB Foot Pedal

A 3-pedal USB footswitch adds hands-free control while playing — sustain, reverb, chorus — without touching the encoder or a MIDI controller.

## Reference hardware

Built and tested against an **iKKEGOL USB Triple Foot Pedal** (Optical Switch, 3-key programmable HID footswitch — sold under various listings, often branded "PCsensor FootSwitch" at the driver/device level). Identified on the Pi as:

```
/dev/input/by-id/usb-PCsensor_FootSwitch-event-kbd
```
USB vendor/product ID: `1a86:e026`

## Default mapping

| Pedal | Sends | Effect |
|---|---|---|
| **Left** | CC 91 + two OSC params | Reverb + soft attack (Scene A & B) |
| **Middle** | CC 93 | Chorus depth |
| **Right** | CC 64 (with fade) | Sustain — see below |

**Sustain fade:** instead of a hard cutoff on release, the right pedal fades sustain out over a configurable duration (default **1.0s**, linear or exponential curve) rather than snapping notes off. Configurable in [`config/pedal-config.json`](../config/pedal-config.json):

```json
{
  "sustain_fade_enabled": true,
  "sustain_fade_duration": 1.0,
  "sustain_fade_curve": "linear"
}
```

## How it works

- [`scripts/pedal-to-osc.py`](../scripts/pedal-to-osc.py) — reads the pedal via `evdev`, bridges to Surge XT over **OSC** (port 53280) as MIDI CC or direct OSC parameter changes
- [`config/foot-pedal.service`](../config/foot-pedal.service) + [`config/99-foot-pedal.rules`](../config/99-foot-pedal.rules) — udev rule auto-starts the bridge when the pedal is plugged in, stops it when unplugged. No always-on process when the pedal isn't connected.

Install:
```bash
scripts/install-foot-pedal-service.sh
```

## Using a different pedal

The framework isn't tied to this specific pedal. `PEDAL_MAPPING` in `pedal-to-osc.py` is a plain dict keyed by evdev keycode — each entry can send:

- a single MIDI CC (`{'cc': 91}`)
- multiple CCs/OSC params at once (`{'multi': [...]}`)
- a raw OSC parameter path with custom on/off values (`{'osc_path': '...', 'value_on': ..., 'value_off': ...}`)

To swap in a different USB footswitch (more/fewer pedals, different brand):

1. Plug it in, find its keycode(s): `evtest` or `sudo libinput debug-events`
2. Update `99-foot-pedal.rules` with the new vendor/product ID (`lsusb` to find them)
3. Update `PEDAL_MAPPING` with the new keycodes and desired CC/OSC targets

No changes needed to Surge XT itself — everything routes through its existing MIDI CC and OSC parameter surface.
