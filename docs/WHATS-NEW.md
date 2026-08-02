# What's new

*Last updated: 2026-08-02 (America/Toronto)*

A plain-English rundown of what changed this week — for anyone following along, not just people reading commit logs. Grouped by what it actually does for you, not by what file it touched.

---

## 🔊 Audio

### Play through your laptop speakers — no aux cable

You can now tether the module to a laptop or desktop over a single USB-C cable and it shows up as a normal audio input, the same way a USB audio interface would. No drivers, no aux cord snaking across the desk, no separate DAC. Plug in, select the module as your input, and you're recording or monitoring straight off the Pi.

This one took real work to get right. Early attempts *looked* perfect — every check passed, the cable was recognized, everything was "connected" — but the host just heard silence. It turned out the audio engine itself was quietly getting stuck mid-stream once the laptop stopped listening for a moment, and it never woke back up on its own. There's now a small watchdog running on the Pi that notices when this happens and kicks the audio engine back into gear automatically, so you never have to think about it. Verified with real playback captured on the host end — clean signal, no glitches.

The setting also now survives a reboot, and switching between "USB to laptop" and "normal analog output" no longer interrupts whatever patch you had loaded.

### Every patch, the same volume

If you've ever loaded a patch that was uncomfortably loud right after one that was nearly silent, that's the exact problem this fixes. Patches are calibrated once (offline, unattended) and remembered — so switching patches doesn't mean constantly grabbing for the volume.

This week closed out a run of loudness bugs that were sneaky because they mostly *looked* like they were working:

- Some genuinely quiet patches (by design — a few patches are quiet on purpose, like ones with inverted velocity) were getting under-corrected because the safety ceiling on the correction was set too conservatively. That ceiling has been raised, and those patches now come back to a normal, listenable level.
- Turning the per-patch "Normalize" toggle off and back on didn't always do what it looked like it was doing — the underlying volume could get stuck at whatever it was before you touched the toggle. Fixed so the toggle now reliably reflects what you see on screen.
- Calibration itself got smarter about patches with a slow attack (think: a pad that fades in over several seconds) — it now gives those patches a longer chance to reach real volume before giving up on measuring them, instead of judging them too early and skipping them.

---

## 🖥️ Touch screen UI

### Browse your entire library, not just favorites

There's a new **All** view that flattens your whole patch library into one alphabetical list with a quick-jump rail on the side — tap a letter, land there instantly. Previously you could only browse folder by folder; now you can just scroll straight to the patch you want by name, from anywhere in the library. Favorited patches show a little heart so you always know what's already in your quick-access folder.

### Make it yours

A proper theme system landed — pick a base look (the original dark theme, or a true-black "OLED" mode that saves power and looks sharp on OLED panels), then pick an accent color from presets or dial in your own exact RGB value and save it for later. It's a small thing, but it's the difference between "a Pi running some software" and an instrument that feels like yours.

### A cleaner settings panel

Settings moved from a popup modal into a slide-out panel that feels more like part of the app and less like an interruption. There's also a small CPU meter now living in the header — handy if you're playing something demanding and want to keep an eye on headroom before things get crackly.

---

## 🛡️ Behind the scenes: boot, shutdown, and reliability

Not the flashy stuff, but it matters if you're actually using this as an instrument you turn on and off like a real piece of gear:

- **Boot and shutdown now show a proper branded splash screen** instead of flashing raw Linux console text at you, on both the way up and the way down.
- Fixed a crash loop where the touch screen could get stuck restarting itself over and over after certain updates — root cause was a stale process holding onto the display it needed.
- Calibration (the "learn each patch's volume" process) had a chain of four separate bugs stacking on top of each other that made one overnight run come back with "0 patches saved." All four are now fixed and covered by automated tests so they can't quietly creep back in.

**136 automated tests** now run on every change before it ships (up from 80 a few days ago) — the safety net behind all of the above.

---

*Want the full engineering log with commit references? See [`CHANGELOG.md`](../CHANGELOG.md). Full technical detail on loudness matching lives in [`PATCH_NORMALIZATION.md`](PATCH_NORMALIZATION.md); on USB desk audio in [`USB-AUDIO-HOST.md`](USB-AUDIO-HOST.md); on the touch screen in [`TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md).*
