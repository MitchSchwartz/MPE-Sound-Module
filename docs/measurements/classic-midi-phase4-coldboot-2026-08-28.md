# Phase 4 — cold boot results

**Date:** 2026-08-28  **Appliance:** pi5, `feat/classic-midi-translator`

Reboots issued over SSH with Mitch's authorisation. Each was verified as a real
cold boot by comparing `/proc/sys/kernel/random/boot_id` before and after, not
by uptime or by trusting the reboot command.

## The four cases

| case | result | how it was tested |
|---|---|---|
| **both attached** | **PASS** | two real cold boots |
| **classic only** | **PASS** | service restarts with the ROLI genuinely off USB — **not** a full cold boot, see below |
| **MPE only** | **NOT TESTED** | would need the APC unplugged |
| **nothing attached** | **NOT TESTED** | approximated only, see below |

### classic only

Verified by restarting the service while the LUMI was powered off and absent
from `lsusb`. The router started, bound the APC, and Surge was reachable. A
full cold boot of this case was started but the LUMI was switched back on
mid-flight, so the reboot became a "both attached" run. **The restart evidence
is real but weaker than a cold boot**, because it does not exercise USB
enumeration ordering at boot.

### nothing attached

Approximated with `MPE_ROUTER_EXCLUDE=apc,lumi,scarlett`, which drives the
"zero bindable ports" code path. It does **not** reproduce USB absence, so
enumeration timing is untested. Treat as code-path coverage only.

## Three real defects this found

None of these were visible from the unit tests; all three needed the appliance.

### 1. The router refused to start without a ROLI

`start-mpe-pressure-remap.sh` exited 0 when no ROLI was on USB — a gate from
when a ROLI was the only bindable device. With classic routing on it meant an
appliance with **only a classic keyboard never started the router at all**, and
because the exit is in the wrapper, before any Python runs, none of the
daemon's wait-for-a-device logic could compensate. Observed directly: the
service reported `no Roli USB — idle exit 0` and went inactive while an APC
sat plugged in and ready.

### 2. `MPE_ROUTER_EXCLUDE` was ignored when opening ports

Port selection was duplicated across two call sites and the exclusion argument
reached only one. The setting applied to the hot-plug check but not the initial
open, so the router kept binding an excluded port with nothing in the log to
say the setting had been disregarded. The unit tests could not have caught it:
they cover `select_router_ports`, which was correct — not the daemon's two
call sites into it. Fixed by collapsing to a single selection helper, so there
is no second site to forget.

### 3. A 32-second startup delay

`wait-for-usb-midi.sh` polls up to 15 s for a ROLI VID and ran **twice** per
start — once in the wrapper, once again in `run()`. With no ROLI attached that
delayed binding by 32 s. Now skipped when classic routing is on, since the
reconnect poll binds devices whenever they appear. Measured after the fix:
**1 s**.

## Incidental

ALSA client numbers changed across reboot (32:1 → 16:1, 36:0 → 20:0,
28:0 → 24:0). Classification is name-based and survived; index-based matching
would have silently bound the wrong devices.

The LUMI disappearing from `lsusb` mid-session was **not** a fault — it was
powered off. Worth remembering before diagnosing a missing ROLI again.
