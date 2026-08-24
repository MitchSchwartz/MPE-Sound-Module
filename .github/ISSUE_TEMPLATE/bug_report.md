---
name: Bug report
about: Something broken on the Pi appliance or touch UI
title: ''
labels: bug
assignees: ''
type: Bug
---

**Describe the bug**
What happened, in plain language. Include the patch or screen if it matters.

**To Reproduce**
Steps to reproduce:
1. 
2. 
3. 

**Expected behavior**
What you expected instead.

**Screenshots / video**
For touch UI glitches, toasts, or layout issues — attach if you can.

**Appliance**

- [ ] Pi 4
- [ ] Pi 5
- [ ] Other (describe)
- UI: [ ] Touch (Freenove DSI)  [ ] Encoder + OLED
- Audio profile: [ ] Standalone (analog/USB DAC)  [ ] USB host  [ ] USB host session
- MPE controller (if relevant): [e.g. Roli LUMI, Seaboard Block]
- Buffer / rate (if you changed them): e.g. 128×2 @ 48 kHz

**Diagnostics (optional but helps)**

Paste output if you can SSH in:

```bash
mpe status          # or: systemctl status surge-xt-cli touch-patch-browser mpe-jackd
mpe diagnose        # read-only snapshot
journalctl -u surge-xt-cli -n 30 --no-pager
journalctl -u touch-patch-browser -n 30 --no-pager
cat /run/mpe/engine.state
```

Git commit on the Pi (`cd ~/MPE-Module && git log -1 --oneline`) if you know it.

**Additional context**
Power supply, recent config changes, hot-plug timing, anything else that might matter.
