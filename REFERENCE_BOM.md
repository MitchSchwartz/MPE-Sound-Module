# Reference BOM — Pi-Surge-MPE (v1)

*Last updated: 2026-07-18 (America/Toronto)*

**Intent:** One blessed hardware stack. Software in this repo is tested against **this** wiring — not arbitrary displays or encoders. See [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) for the canonical diagram (includes fan on pins 1 & 6).

> Links below are **Amazon.ca** (Mitch’s market). Same ASINs often work on `.com`; search the ASIN if the link doesn’t resolve in your region.

---

## Required — UI (patch browser)

| Qty | Part | Spec | Buy (CA) | Notes |
|-----|------|------|----------|-------|
| 1 | **1.3″ I2C OLED** | Walfront 128×64, blue, I2C `0x3C`, 4-pin header | [B0B5HRB59Q](https://www.amazon.ca/dp/B0B5HRB59Q) | **Confirmed part:** *OLED Display 1.3 Inch 128X64 IIC I2C SPI OLED Display Module* (Walfront). Wire **I2C only** (VCC/GND/SCL/SDA) — ignore SPI pins if present. Controller is typically **SH1106** (code falls back to SSD1306). Seller ref: `ame8xh1pwt27589`. Verify: `sudo i2cdetect -y 1` → `3c`. |
| 1 | **KY-040 rotary encoder module** | WMYCONGCONG, CLK/DT/SW/+/GND | [B07FJQH1F7](https://www.amazon.ca/dp/B07FJQH1F7) (5-pack + jumpers) | **Reference build only — weak recommendation.** UPC `705169048923`. Cheap module; works with this repo’s debounce stack (~95% feel) but bouncy and inconsistent unit-to-unit. Use **one** from the pack; **do not connect `+` (VCC)** — [`docs/ENCODER_NO_VCC.md`](docs/ENCODER_NO_VCC.md). Prefer a better encoder if you care about tactile quality. |
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

**Audio path:** Surge XT CLI → ALSA → Sound Blaster USB → analog out. No GPIO audio HAT required.

---

## Optional / existing on Mitch’s build

| Part | Notes |
|------|-------|
| Case fan on pins 1 & 6 | Drives the “encoder without VCC” wiring constraint |
| 7″ screen | **Not supported** by patch browser UI — separate Surge GUI path if ever used |

---

## Software side of “the knob design”

Hardware is only half of it. This repo includes **encoder/button filtering** tuned for a cheap KY-040:

- [`patch_browser_ui.py`](patch_browser_ui.py) — `Config` debounce times, evdev + gpiozero paths, dialog/power-menu state machine
- [`docs/ENCODER_NO_VCC.md`](docs/ENCODER_NO_VCC.md) — why VCC is left floating
- [`ENCODER_BUTTON_REVIEW.md`](ENCODER_BUTTON_REVIEW.md) — known UX edge cases and fixes

Expect ~**95% reliable** mechanical feel with the **WMYCONGCONG KY-040** above — mostly software, not hardware. A nicer encoder may still need tuning but starts from a better baseline.

---

## Part IDs (reference build)

| Part | ASIN / ID | Brand |
|------|-----------|-------|
| OLED 1.3″ 128×64 | B0B5HRB59Q | Walfront |
| KY-040 module | B07FJQH1F7 · UPC 705169048923 | WMYCONGCONG |
| Sound Blaster Play! 3 | B06XBZ38ZJ (label: B06XBZ38ZJ1) | Creative · SB1730 |

---

## What this BOM replaces

| File | Status |
|------|--------|
| [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) | **Canonical** wiring for v1 (1 OLED + 1 encoder) |
| [`WIRING_DIAGRAM.txt`](WIRING_DIAGRAM.txt) | **Stale** — describes old 5-encoder + JACK plan; ignore for v1 |
| [`HARDWARE.md`](HARDWARE.md) | **Generic** early project doc; use this file + HARDWARE_WIRING for builds |

---

## Still to add

- [ ] Photo of **your** actual wired stack (for forum posts)
- [ ] Optional: 3D-print / enclosure note
