# Third-party notices

*Last updated: 2026-08-23*

This project is a headless Raspberry Pi wrapper around software written by other people.
This file records what it ships, under what terms, and where the corresponding source is.

**This repo's own code** is licensed separately — see [`LICENSE`](LICENSE) (PolyForm
Noncommercial 1.0.0). That license applies to the Pi setup, wiring, UI, measurement harness,
and deploy scripts in this repository. It does **not** apply to any of the components below.

---

## Distribution status

**No GPL binary has been conveyed to a third party.** The compiled Surge XT binary lives
only in the private `MPE-Library` assets repo and on the maintainer's own boards. The public
`MPE-Sound-Module` repository contains **no** Surge binary.

**The obligations in this file attach the moment a built SD card, `.img.xz`, or compiled
binary is given to anyone else** — including for free, including to a friend. See
[`docs/PI4-CLONE-SD.md`](docs/PI4-CLONE-SD.md).

---

## Components

| Component | License | Shipped as | Source |
|---|---|---|---|
| **Surge XT** | GPL-3.0-or-later | Prebuilt `surge-xt-cli` binary | [surge-synthesizer/surge](https://github.com/surge-synthesizer/surge) @ **`253f8d86`** |
| **JUCE** (via Surge) | GPL-3.0 option | Statically linked into Surge | Surge submodule at the pinned commit |
| **SooperLooper** 1.7.9 | GPL-2.0 | Optional; built from source on the unit | [sooperlooper.com](http://essej.net/sooperlooper/) · tag `v1.7.9` |
| **JACK2** (`jackd2`) | GPL-2.0 (server) | Debian package | Debian archive |
| **libjack** | LGPL-2.1 | Dynamically linked by `mpe-peak-meter`, `mpe-xrun-probe` | Debian archive |
| **pygame** | LGPL-2.1 | Debian package (touch UI) | Debian archive |
| **python-rtmidi** | MIT | Debian package `python3-rtmidi` | Debian archive |
| **Raspberry Pi OS / Debian** | Mixed | Base OS | Debian archive |

Debian-packaged components carry their own copyright files under `/usr/share/doc/<pkg>/copyright`
on the appliance; nothing here replaces those.

---

## Surge XT — corresponding source

Surge is built **unmodified** from upstream at a pinned commit. The build is not a plain
upstream build — it uses a reduced target set and, for some variants, an `-mcpu` flag — so
**the build script is part of the required corresponding source**, not an extra.

| Field | Value |
|---|---|
| Upstream | `https://github.com/surge-synthesizer/surge.git` |
| Commit | **`253f8d86`** |
| Build script | [`scripts/build-surge.sh`](scripts/build-surge.sh) |
| Variants | `--arch generic` · `--arch a72` (Pi 4) · `--arch a76` (Pi 5) |
| CMake flags | `-DCMAKE_BUILD_TYPE=Release -DLINUX_ON_ARM=TRUE -DSURGE_BUILD_{LV2,VST3,CLAP,TESTRUNNER}=FALSE -DSURGE_BUILD_STANDALONE=TRUE` (+ `-mcpu=` per variant) |

Reproduce with:

```bash
./scripts/build-surge.sh --arch a76 --commit 253f8d86
```

**If Surge is ever patched locally, this section stops being sufficient** — pointing at
upstream only works while the source is unmodified. Modified source must then be conveyed
directly.

**The binary in `MPE-Library/assets/binaries/` was built for the Pi 4 and is currently also
in use on the Pi 5.** The `-mcpu=cortex-a76` build is not yet the shipped artifact. Whichever
binary ships is the one this provenance must describe — verify before distributing.

---

## On-appliance compliance payload

[`scripts/install-license-payload.sh`](scripts/install-license-payload.sh) installs, under
`/usr/share/doc/mpe/licenses/`:

- GPL-3.0, GPL-2.0 and LGPL-2.1 texts (from Debian's `/usr/share/common-licenses`)
- `CORRESPONDING-SOURCE.md` — upstream URL, pinned commit, build flags
- a copy of `build-surge.sh`
- `PROVENANCE.txt` — the **installed** binary's version and sha256, stamped at install time

This discharges the corresponding-source duty **at handoff**, rather than relying on a
written offer under GPL-3.0 §6(b), which would bind the maintainer to supply source to
anyone who asks for three years.

Run `sudo ./scripts/install-license-payload.sh --verify` before imaging.

---

## Installation Information (GPL-3.0 §6)

The appliance is a general-purpose Linux system: the SD card is writable, SSH is available,
and the Surge binary can be replaced in place. No signature check or verified boot restricts
installing a modified Surge.

**This is a design constraint, not just a statement of fact.** Adding image signing, a
read-only rootfs with verified boot, or any lock-down that prevents installing a modified
Surge would create a GPL-3.0 §6 obligation to supply Installation Information. Decide that
deliberately if it ever comes up.

---

## Separation from this project's code

MPE-Module drives Surge as a **separate process** over OSC, MIDI and JACK. It does not link
Surge as a library, embed its DSP, or host it as a plugin. On that basis the two are separate
works and this repository's PolyForm Noncommercial license does not conflict with Surge's
GPL-3.0.

**That boundary is load-bearing.** Linking Surge as a library, embedding its DSP, or hosting
it in-process would make the result a combined work that must be GPL-3.0 — and PolyForm
Noncommercial, which restricts commercial use, is incompatible with GPL-3.0's prohibition on
adding restrictions. Keep the arms-length relationship or change this repo's license
deliberately.

---

## Patches and content

Patch content carries **its own terms**, separate from the code licenses above, and it
travels with the card because `deploy-all.sh` copies it onto the image.

| Pack | License |
|---|---|
| Italo Disco Drum Pack 1 | CC0 |
| ironcross32 / Surge-XT-Patches | CC0-1.0 (`assets/Surge-XT-Patches-main/LICENSE`) |
| Phasor Space Presets Vol. 1 | CC0 |
| Hefxthoth collection | DWTFYWPL |

All four are public-domain-equivalent or unconditionally permissive, so they impose no
distribution obligation. Credits are maintained in the `MPE-Library` README
(§ Third-party patch credits).

**Still to confirm before distribution:** the terms covering **Surge XT factory content**
(shipped with the Surge binary, not one of the packs above), and whether the **Quick Select**
tree contains anything beyond the four packs and your own patches.

## Attribution

**Surge XT** is built by the [Surge Synth Team](https://surge-synthesizer.github.io/),
originally released under GPL-3.0 by Claes Johanson / Vember Audio in 2018. None of the sound
engine, MPE handling, or patch format is this project's work.
