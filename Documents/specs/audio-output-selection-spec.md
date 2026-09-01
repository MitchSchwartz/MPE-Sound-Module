# Audio output selection

Status: Proposed, not implemented. Written 2026-09-01 after a full day lost to
three separate bugs that were all the same bug.

This file exists because the appliance has never had a stated audio output. It
has had a *guess*, re-derived from scratch on every start by whichever heuristic
was nearest to hand, and the guess was wrong in a different way each time. Every
section below names the heuristic it replaces and the failure that heuristic
produced on the appliance.

If you are about to add a device-selection heuristic, this is the file that says
why you should not.

---

## 1. The three failures this replaces, all measured 2026-09-01

They look unrelated. They are one defect: **the device was chosen implicitly.**

| heuristic | where | what it actually selected |
|---|---|---|
| `grep -i "JACK" \| head -1` | `start-surge-cli.sh` | a DAC named "USB-C to 3.5mm Headphone **Jack** A" |
| `hw:N` as an identity | detection, `jack.state` | whatever card held index N this boot |
| first playable card wins | tier detection | `snd-dummy`, which is inaudible by construction |

The first is the one that cost the day. `surge-xt-cli --list-devices`, verbatim:

```
[0.8]  : ALSA.USB-C to 3.5mm Headphone Jack A, ... Direct hardware device
[0.9]  : ALSA.USB-C to 3.5mm Headphone Jack A, ... Front output / input
...eight more ALSA entries for the same device...
[1.0]  : JACK.system
```

Ten ALSA lines matched on the product name before the one real JACK device, and
`head -1` took `[0.8]`. Surge opened the raw ALSA device, which does not support
48000 Hz, and exited — while `engine.state` read `engine=jack active=jack
state=ok` and the journal logged "JACK client". Surge never appeared in
`jack_lsp`.

**The Scarlett 4i4 has no "Jack" in its name.** That, and nothing else, is why
the same commit worked with one DAC and failed with the other. The failure was a
property of the product name, which is exactly why it read as a code regression
and was not one.

An explicit selection makes all three impossible, because there is nothing left
to infer.

---

## 2. Identity: what names a device

The assumption to kill: **that an ALSA card id identifies anything.** It does
not. Measured on the appliance:

| device | card id | product string |
|---|---|---|
| Focusrite Scarlett 4i4 | `USB` | `Scarlett 4i4 USB` |
| Apple USB-C dongle | `A` | `USB-C to 3.5mm Headphone Jack A` |

`USB` and `A`. Neither is descriptive, neither is unique, and two identical DACs
would collide outright. Card *indices* are worse: `hw:0` was the Apple dongle at
12:00 and the Scarlett at 15:02, same boot.

A stored selection MUST key on, in order of preference:

1. **`idVendor:idProduct` plus `serial`** where the device exposes one. Read from
   the USB device directory, not from ALSA.
2. **`idVendor:idProduct`** alone where it does not. Ambiguous only between two
   of the same model, which is a case to detect and surface, not to guess at.
3. Never the card index. Never the card id. Never the product string.

The product string is a **display label only**. It is attacker-shaped input in
the sense that matters here: a vendor can put any word in it, including "JACK".

`/proc/asound/cardN/usbid` gives `VID:PID`. The USB device directory gives
`serial`, `speed`, and `product`.

---

## 3. What the menu shows

One row per **physical playback device**, plus the two special entries below.

Each row shows:

- the product string as the label,
- the enumerated USB **speed** (`480M` / `12M`),
- whether it is the currently bound device,
- whether it is currently **present**.

Speed is on the row because it is the single strongest predictor of what period
the device can run, and it is otherwise invisible. Measured 2026-09-01: the
Apple dongle enumerates **full-speed** (12 Mbps, 1 ms service interval = 48
frames at 48 kHz) and cannot start a driver at a 64-frame period; the Scarlett
enumerates **high-speed** (480 Mbps, 125 µs microframes = 6 frames) and runs 64
without complaint. A user staring at "no sound" should be able to see that fact
rather than discover it through a day of bisection.

Virtual cards are **excluded** from the list by `mpe_card_is_virtual()` — the
existing single predicate. Do not write a second list. That predicate exists
because five hand-maintained "which cards are virtual" lists had already
diverged (587852b).

### The two special entries

| entry | meaning |
|---|---|
| **Automatic** | current tier-detection behaviour. The default. |
| **Silent (no output)** | deliberately bind the idle sink. |

"Automatic" must remain the default and must remain first. Most of the time
tier detection is right, and a device chosen once at a rehearsal should not
become a trap six months later when that DAC is in another bag.

"Silent" exists so that binding the idle sink is a **stated intent** rather than
an accident. Today it is only ever reached by accident, and that is the state
the appliance reports as `state=ok`.

---

## 4. Behaviour when the chosen device is absent

This is the section that matters. Get it wrong and the whole feature is a
liability.

**The appliance MUST NOT silently substitute another device.**

| situation | required behaviour |
|---|---|
| chosen device present | bind it |
| chosen device absent | bind the idle sink, report `audible=no`, say *which* device is missing, by name |
| chosen device absent, "Automatic" selected | existing tier detection |
| two devices match an ambiguous identity | surface it; do not pick one |

The reason is the whole theme of 2026-09-01: the appliance failed by falling back
to something inaudible **while reporting green**. `jack.state` already carries
`card=`, `tier=` and `audible=` for exactly this. The HUD already reads
`audible=no` and shows "No audio output — connect a DAC". A selection feature
must feed those fields, not bypass them.

A missing *chosen* device is a stronger signal than a missing *any* device: the
user told us what they wanted and it is not here. Say so by name — "Scarlett 4i4
not connected", not "no audio output".

---

## 5. Applying a change

Reuse `set-surge-audio.sh`'s machinery; do not write a second path. It already
has, as of 2026-09-01:

- `flock` serialisation on a shared lock file,
- a crash marker on **persistent** storage (`/etc/mpe/mpe.env.pending`, not
  `/run` — tmpfs is wiped by the reboot the marker must survive),
- reconciliation from `mpe-jackd`'s `ExecStartPre`.

An output change restarts the graph exactly as a buffer change does, so it needs
all three. `set-audio-profile.sh` was the second door into the same failure and
had none of them; a third door must not be opened.

**Known gap:** that rollback path does not currently recover. Observed
2026-09-01: `ERROR: graph failed and rollback failed`, leaving the appliance
silent. Fix that before routing more mutations through it.

---

## 6. The buffer list is the same bug, and should be fixed with it

The settings menu cannot currently offer periods the appliance accepts. Four
lists, three wrong:

| location | list |
|---|---|
| `scripts/lib/audio-engine.sh:45` | 32, 64, **96**, 128, **192**, 256, 512, 1024 ← the truth |
| `scripts/set-surge-audio.sh:50` | identical duplicate |
| `patch_browser/surge_audio.py:16` | 32, 64, 128, 256, 512, 1024 — missing 96, 192 |
| `mpe-cli/commands/jack.sh:17` | 64, 128, 256, 512, 1024 — missing 32, 96, 192 |

`96` and `192` are runnable on the appliance today and unreachable from every
user interface. They are also the two values that are **exactly aligned to a
full-speed USB frame** at 48 kHz (2 ms and 4 ms), which is precisely the regime
where the Apple-class dongles live.

There should be **one** list, exported from `audio-engine.sh`, consumed by the
Python UI and by mpe-cli the way mpe-cli already sources `mpe_card_is_virtual`
over SSH rather than mirroring it.

### On adding 48

`48` is one full-speed USB frame at 48 kHz and is not currently in any list.
Before adding it, note what the archive already measured:

> **T13 (2026-08-21): period size binds, not total buffer.** `128×6` and `256×3`
> carry identical 768-frame runways and measured 713 vs 1.53 xruns/60 s.

Shrinking the period is the expensive direction, and 64 already fails on the
full-speed dongle. `48` is therefore a **measurement**, not a setting to expose.
T12's alignment experiment was retracted as confounded, so "aligned" is an open
hypothesis, not an established defence. Run the ladder before putting 48 in a
menu where it can be selected mid-gig.

---

## 7. Out of scope

- Input/capture selection. The Scarlett has inputs; nothing in the audio path
  uses them today.
- Per-patch or per-set output routing.
- Channel mapping beyond `out_1/out_2 → playback_1/2`.

---

## 8. Open questions for Mitch

1. **Should a chosen-but-absent device block startup, or fall through to
   Automatic?** Blocking is honest; falling through means the appliance always
   makes noise. My inclination is fall through to Automatic *and say loudly what
   happened*, because a rig that is silent at soundcheck is worse than one that
   is on the wrong output and says so.
2. **Should the selection survive a reflash?** It lives in `mpe.env` either way;
   the question is whether it belongs in the backed-up set.
3. **Does `state=` need a fourth value meaning "running but inaudible"?**
   Deliberately unresolved since 2026-09-01: `degraded` is retired and
   lint-banned by `lint-jack-only-paths.sh:56`. Currently expressed as
   `jack.state audible=no` with `state=ok`, which is honest but requires two
   reads to interpret.
