# Reference BOM — MPE Sound Module

*Last updated: 2026-08-23 (America/Toronto)* Software is tested against **these** stacks — not arbitrary displays or encoders.

| Path | Status | Doc |
|------|--------|-----|
| **Touch (Freenove 5″)** | **Recommended** player build; Pi 5 tuning/validation in progress | [`docs/TOUCH_PATCH_BROWSER.md`](docs/TOUCH_PATCH_BROWSER.md) |
| **Encoder + OLED** | Legacy — works, not gig-polished | [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) |

**Platform:** Pi **5** is the preferred player (more headroom, lower JACK buffers in daily use). Pi **4** remains the certified measurement control and clone-SD reference until the Pi 5 reference suite closes — [`docs/measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md`](docs/measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md).

> Links below are **Amazon.ca** (Mitch's market). Same ASINs often work on `.com`; search the ASIN if the link doesn't resolve in your region.

---

## Touch build (recommended)

### Display

| Qty | Part | Spec | Buy (CA) | Notes |
|-----|------|------|----------|-------|
| 1 | **Freenove 5″ IPS DSI touch panel** | 800×480, 5-point capacitive, MIPI DSI, FNK0078A | [B0B455LDKH](https://www.amazon.ca/dp/B0B455LDKH) | **Confirmed reference panel.** Pi-only — no HDMI. Includes ribbon cables + stand. Seller: Freenove. |
| — | **Touch IC** | **EDT FT5x06** | *(on panel)* | Linux driver **`edt_ft5x06`**; evdev at `/dev/input/event*` on reference units |
| 0–1 | **SmartiPi Touch case** *(optional)* | Pi 4 or Pi 5 enclosure | [SmartiPi shop](https://smarticase.com/) | Reference bench uses Freenove panel; case is optional if you want a enclosed stack |

**Connection:** DSI ribbon to **CAM/DISP** (Pi 5) or **DISPLAY** (Pi 4). Freenove ships driver-free on current Pi OS; reference units also set `dtoverlay=vc4-kms-dsi-7inch` — see [`docs/PI5-PLAYER-SETUP-LOG.md`](docs/PI5-PLAYER-SETUP-LOG.md) §C and verify with `kmsprint`.

**Not validated:** generic HDMI+USB touch monitors; **7″** Freenove SKUs (layout targets 800×480 landscape).

### Compute + audio (touch)

| Qty | Part | Spec | Buy (CA) | Notes |
|-----|------|------|----------|-------|
| 1 | **Raspberry Pi 5** | **4 GB** recommended; Pi OS **Lite** 64-bit | *(Pi retailer)* | **Preferred player.** Reference unit: 128×2 @ 48 kHz (Aug 2026). |
| 1 | **Raspberry Pi 4 Model B** | **4 GB** (reference unit) | *(Pi retailer)* | Certified measurement + clone-SD baseline — **not** 8 GB |
| 1 | **USB audio dongle** | Creative **Sound Blaster Play! 3** (SB1730) | [B06XBZ38ZJ](https://www.amazon.ca/dp/B06XBZ38ZJ) | Same as encoder build — USB stick, not a DAC HAT |
| 1 | **microSD** | 32 GB+, UHS-I | *(retailer)* | See [`docs/STORAGE-ROBUSTNESS.md`](docs/STORAGE-ROBUSTNESS.md) |
| 1 | **Pi power supply** | **Official 27 W (5 V / 5 A) on Pi 5**; 3 A OK on Pi 4 | *(retailer)* | Pi 5 reference suite blocked on undersized 5 V / 3 A supply + no active cooler |
| — | **Power routing** | USB-C to Pi **works** | — | **Recommended:** feed **5 V/GND on GPIO** (or a barrel PSU) so **USB-C stays free** for desk tether + `MPE_AUDIO_PROFILE=usb-host` streaming to a PC. PD on the same port as gadget data can block attach on Pi 4 — [`docs/USB-AUDIO-PASSTHROUGH-SPIKE.md`](docs/USB-AUDIO-PASSTHROUGH-SPIKE.md) |
| 1 | **USB cable** | Pi ↔ Roli Seaboard | *(retailer)* | MPE MIDI input |
| 1 | **USB-C cable** *(desk kit)* | Host PC ↔ Pi USB-C | *(retailer)* | Data only when Pi is GPIO-powered; for `usb-host` — [`docs/USB-AUDIO-HOST.md`](docs/USB-AUDIO-HOST.md) |

**No OLED or KY-040** on the touch build.

### RAM

| RAM | Touch + Surge (no looper) | With SooperLooper |
|-----|---------------------------|-------------------|
| **4 GB** | **Reference** — certified Pi 4 player path | Tight but measured on eval bench |
| **2 GB** | **Probably not** — untested; Surge + pygame + OS leaves little margin; memory pressure can show up as xruns before OOM | **No** — add ~150 MB+ for the engine alone |

Use a **prebuilt Surge binary** on low-RAM boards (do not compile on the Pi). No swap on a realtime instrument unless you are doing a one-off build and remove it after.

---

## Encoder + OLED build (legacy)

See [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) for the canonical wiring diagram (includes fan on pins 1 & 6).

### Required — UI (patch browser)

| Qty | Part | Spec | Buy (CA) | Notes |
|-----|------|------|----------|-------|
| 1 | **1.3″ I2C OLED** | Walfront 128×64, blue, I2C `0x3C`, 4-pin header | [B0B5HRB59Q](https://www.amazon.ca/dp/B0B5HRB59Q) | **Confirmed part:** *OLED Display 1.3 Inch 128X64 IIC I2C SPI OLED Display Module* (Walfront). Wire **I2C only** (VCC/GND/SCL/SDA) — ignore SPI pins if present. Controller is typically **SH1106** (code falls back to SSD1306). Verify: `sudo i2cdetect -y 1` → `3c`. |
| 1 | **KY-040 rotary encoder module** | WMYCONGCONG, CLK/DT/SW/+/GND | [B07FJQH1F7](https://www.amazon.ca/dp/B07FJQH1F7) (5-pack + jumpers) | **Weak recommendation.** Cheap, bouncy, no normal click. **Do not connect `+` (VCC)** — [`docs/ENCODER_NO_VCC.md`](docs/ENCODER_NO_VCC.md). |
| ~8 | **Jumper wires** | Female–female, 2.54 mm | *(included in encoder listing above)* | OLED: 4 wires. Encoder: 3 signals + GND on **its own** Pi GND pin. |

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

**GND:** one jumper per module to its **own** Pi GND pin (reference: OLED → 9, encoder → 14).

**Reserved:** pins **1** (3.3V) and **6** (GND) — **case fan only; do not use.**

### Required — audio & compute (encoder)

| Qty | Part | Spec | Buy (CA) | Notes |
|-----|------|------|----------|-------|
| 1 | **Raspberry Pi 5** (or 4) | **4 GB**; Pi OS **Lite** 64-bit | *(Pi retailer)* | Pi 5 preferred; Pi 4 reference unit is **4 GB** |
| 1 | **USB audio dongle** | Creative **Sound Blaster Play! 3** (SB1730) | [B06XBZ38ZJ](https://www.amazon.ca/dp/B06XBZ38ZJ) | USB stick — not a DAC HAT |
| 1 | **microSD** | 32 GB+, UHS-I | *(retailer)* | Flash Pi OS Lite; deploy via repo scripts |
| 1 | **Pi power supply** | Official 5 V / 5 A (Pi 5) or 3 A (Pi 4) | *(retailer)* | Undervoltage = audio glitches |
| 1 | **USB cable** | Pi ↔ Roli Seaboard | *(retailer)* | MPE MIDI input |

**Audio path (standalone):** Surge XT CLI → ALSA → Sound Blaster USB → analog out.

---

## Optional / not in reference build

| Part | Notes |
|------|-------|
| Case fan on pins 1 & 6 | Encoder build — drives the “encoder without VCC” wiring constraint |
| **7″ DSI panel** | Not supported by touch patch browser layout (800×480 target) |
| **HDMI touch monitor** | Not validated on this stack |
| HiFiBerry / DAC HAT | USB dongle or Sound Blaster path is reference |
| Second encoder | Future UX; not wired in reference |
| USB foot pedal | Optional — [`docs/FOOT_PEDAL.md`](docs/FOOT_PEDAL.md) |

---

## Software side of “the knob design” (encoder build only)

- [`patch_browser_ui.py`](patch_browser_ui.py) — debounce, evdev + gpiozero paths, hold-based button model
- [`docs/ENCODER_NO_VCC.md`](docs/ENCODER_NO_VCC.md) — why VCC is left floating
- [`docs/PATCH_BROWSER_UI.md`](docs/PATCH_BROWSER_UI.md) — honest interaction model
- [`ENCODER_BUTTON_REVIEW.md`](ENCODER_BUTTON_REVIEW.md) — known issues

---

## Part IDs (encoder build)

| Part | ASIN / ID | Brand |
|------|-----------|-------|
| Freenove 5″ DSI touch | B0B455LDKH · FNK0078A | Freenove |
| OLED 1.3″ 128×64 | B0B5HRB59Q | Walfront |
| KY-040 module | B07FJQH1F7 · UPC 705169048923 | WMYCONGCONG |
| Sound Blaster Play! 3 | B06XBZ38ZJ (label: B06XBZ38ZJ1) | Creative · SB1730 |

---

## Related docs

| Doc | Purpose |
|-----|---------|
| [`docs/TOUCH_PATCH_BROWSER.md`](docs/TOUCH_PATCH_BROWSER.md) | Freenove 5″ touch setup, evdev, services |
| [`docs/PI5-PLAYER-SETUP-LOG.md`](docs/PI5-PLAYER-SETUP-LOG.md) | Pi 5 player bringup from Pi 4 reference |
| [`docs/PI4-CLONE-SD.md`](docs/PI4-CLONE-SD.md) | Clone SD for Pi 4 touch reference |
| [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) | Encoder/OLED wiring diagram |
| [`docs/BUILD-FROM-ZERO.md`](docs/BUILD-FROM-ZERO.md) | Software from blank SD |

This file and [`docs/HARDWARE_WIRING.md`](docs/HARDWARE_WIRING.md) are the canonical hardware reference.
