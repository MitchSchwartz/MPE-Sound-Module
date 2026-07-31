# Reference BOM — Pi-Surge-MPE (v1)

*Last updated: 2026-07-18 (America/Toronto)*

**Intent:** One blessed hardware stack. Software in this repo is tested against **this** wiring — not arbitrary displays or encoders. See [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) for the canonical diagram (includes fan on pins 1 & 6).

> Links below are **Amazon.ca** (Mitch’s market). Same ASINs often work on `.com`; search the ASIN if the link doesn’t resolve in your region.

---

## Required — UI (patch browser)

| Qty | Part | Spec | Buy (CA) | Notes |
|-----|------|------|----------|-------|
| 1 | **1.3″ I2C OLED** | Walfront 128×64, blue, I2C `0x3C`, 4-pin header | [B0B5HRB59Q](https://www.amazon.ca/dp/B0B5HRB59Q) | **Confirmed part:** *OLED Display 1.3 Inch 128X64 IIC I2C SPI OLED Display Module* (Walfront). Wire **I2C only** (VCC/GND/SCL/SDA) — ignore SPI pins if present. Controller is typically **SH1106** (code falls back to SSD1306). Seller ref: `ame8xh1pwt27589`. Verify: `sudo i2cdetect -y 1` → `3c`. |
| 1 | **KY-040 rotary encoder module** | WMYCONGCONG, CLK/DT/SW/+/GND | [B07FJQH1F7](https://www.amazon.ca/dp/B07FJQH1F7) (5-pack + jumpers) | **Reference build only — weak recommendation.** Cheap, bouncy, no normal click (short taps ignored). Software mostly fixes **false presses**, not navigation quality. **Do not connect `+` (VCC)** — [`docs/ENCODER_NO_VCC.md`](docs/ENCODER_NO_VCC.md). Prefer a better encoder if you care about feel. |
| ~8 | **Jumper wires** | Female–female, 2.54 mm | *(included in encoder listing above)* | OLED: 4 wires. Encoder: 3 signals + GND on **its own** Pi GND pin (see wiring). |

### Wiring (summary)

| From | To (Pi physical pin) | GPIO |
|------|----------------------|------|
| OLED VCC | 17 | 3.3V |
| OLED GND | 9 | GND |
| OLED SDA | 3 | GPIO 2 |
| OLED SCL | 5 | GPIO 3 |
| Encoder CLK | 11 | GPIO 17 |
| Encoder DT | 13 | GPIO 27 |
| Encoder SW | 15 | GPIO 22 |
| Encoder GND | 14 | GND |
| Encoder **+** | *(not connected)* | — |

**GND:** one jumper per module to its **own** Pi GND pin (reference build: OLED → 9, encoder → 14). All Pi GND pins are common internally — do **not** daisy-chain or tie both modules to one pin unless you prefer that layout.

**Reserved:** pins **1** (3.3V) and **6** (GND) — **case fan only; do not use.**

Full ASCII diagram: [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md)

---

## Required — audio & compute

| Qty | Part | Spec | Buy (CA) | Notes |
|-----|------|------|----------|-------|
| 1 | **Raspberry Pi 5** (or 4, 4GB+) | Pi OS **Lite** 64-bit | *(Pi retailer)* | Tested on Pi 5. |
| 1 | **USB audio dongle** | Creative **Sound Blaster Play! 3** (SB1730) | [B06XBZ38ZJ](https://www.amazon.ca/dp/B06XBZ38ZJ) | **USB stick — not a DAC HAT.** ASIN `B06XBZ38ZJ` (label may also show `B06XBZ38ZJ1`). Plug into Pi USB; 3.5 mm out to amp/headphones. Scripts prefer **Front output**. Play! 4 also works; v1 BOM is Play! 3. |
| 1 | **microSD** | 32GB+, UHS-I | *(retailer)* | Flash Pi OS Lite; deploy via repo scripts. |
| 1 | **Pi power supply** | Official 5V 5A (Pi 5) or 3A (Pi 4) | *(retailer)* | Undervoltage = audio glitches. |
| 1 | **USB cable** | Pi ↔ Roli Seaboard | *(retailer)* | MPE MIDI input. |
| 1 | **USB-C cable** *(desk kit)* | Host PC ↔ Pi USB-C | *(retailer)* | **Data-capable.** For `MPE_AUDIO_PROFILE=usb-host` — Surge → host speakers without aux. Prefer USB-A → USB-C on Pi 5 + Mac. See `docs/USB-AUDIO-HOST.md`. |

**Audio path (standalone):** Surge XT CLI → ALSA → Sound Blaster USB → analog out. No GPIO audio HAT required.

**Audio path (usb-host, optional):** Surge XT CLI → ALSA → UAC2 gadget → USB-C → host PC.

---

## Optional / existing on Mitch’s build

| Part | Notes |
|------|-------|
| Case fan on pins 1 & 6 | Drives the “encoder without VCC” wiring constraint |
| 7″ screen | **Not supported** by patch browser UI — separate Surge GUI path if ever used |

---

## Software side of “the knob design”

Hardware is only half of it. This repo includes filtering tuned for a cheap KY-040:

- [`patch_browser_ui.py`](patch_browser_ui.py) — debounce, evdev + gpiozero paths, hold-based button model
- [`docs/ENCODER_NO_VCC.md`](docs/ENCODER_NO_VCC.md) — why VCC is left floating
- [`docs/PATCH_BROWSER_UI.md`](docs/PATCH_BROWSER_UI.md) — honest interaction model (no normal click; holds only)
- [`ENCODER_BUTTON_REVIEW.md`](ENCODER_BUTTON_REVIEW.md) — known issues; next upgrade is separate enter (~1s) / back (~3s) holds

Do not expect tight scrolling or tap-to-confirm on the stock KY-040. The stack mainly suppresses false presses.

---

## Part IDs (reference build)

| Part | ASIN / ID | Brand |
|------|-----------|-------|
| OLED 1.3″ 128×64 | B0B5HRB59Q | Walfront |
| KY-040 module | B07FJQH1F7 · UPC 705169048923 | WMYCONGCONG |
| Sound Blaster Play! 3 | B06XBZ38ZJ (label: B06XBZ38ZJ1) | Creative · SB1730 |

---

This file and [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) are the canonical hardware reference for v1 (1 OLED + 1 encoder). There's no other BOM or wiring doc in this repo — this is it.

---

## Still to add

- [ ] Photo of **your** actual wired stack (for forum posts)
- [ ] Optional: 3D-print / enclosure note
