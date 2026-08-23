# Pi 5 IRQ and core allocation — investigation plan

*Created: 2026-08-23 (America/Toronto)*

**Goal:** Decide whether Pi 4's `irqaffinity=0,1` + `CPUAffinity=2-3` model helps or hurts on
BCM2712/RP1, and what levers actually exist — before Suite 1 or further tuning.

**Context:** Pi 4 census and `apply-movable-irq-affinity.sh` (IRQs 41/42/43/28/57) do **not**
transfer. Early Pi 5 snapshot (2026-08-23) shows Sound Blaster on **RP1 xhci usb1** (IRQ **131**),
Wi‑Fi/SDIO on **mmc1** (IRQ **162**), and **`smp_affinity_list` not writable** on those lines.

**Wi‑Fi stays on** (not Ethernet-only). Bluetooth disabled separately (2026-08-23 hygiene pass).

Canon: [`PI5-TRANSITION-PLAN.md`](../PI5-TRANSITION-PLAN.md) · [`PROMPT-PI5-DAY0.md`](PROMPT-PI5-DAY0.md) §3 ·
[`PI5-HYGIENE-AND-CONFIG-PLAN.md`](PI5-HYGIENE-AND-CONFIG-PLAN.md) ·
Pi 4 [`cpu-census-2026-08-21.md`](archive/cpu-census-2026-08-21.md).

---

## Phase 0 — Baseline capture (census only, no tuning)

**Rule:** one variable per phase after this. No affinity changes during Phase 0.

| # | Capture | Command / artifact |
|---|---------|-------------------|
| 0.1 | Full interrupt map idle | `/proc/interrupts` → `appliance-state/pi5-irq-census-YYYYMMDD/idle.txt` |
| 0.2 | Interrupt map under load | Same during Surge `@64` poly or reference-suite cell A (~60 s) → `loaded.txt` |
| 0.3 | USB topology | `lsusb -t`, note Sound Blaster bus/port (expect **Bus 001 / usb1**) |
| 0.4 | Per-IRQ affinity | For each line in {131, 136, 106, 161, 162, 148 dsi}: `effective_affinity_list`, `smp_affinity_list`, writable? |
| 0.5 | Cmdline + units | `cmdline.txt`, `config.txt`, `systemctl show … CPUAffinity` for audio units |
| 0.6 | Softirq breakdown | `cat /proc/softirqs` idle + loaded |
| 0.7 | Thread placement | `ps -eLo pid,cls,pri,psr,comm \| grep -E 'jackd|surge|FF'` under load |

**Deliverable:** `appliance-state/pi5-irq-census-YYYYMMDD/README.md` with a table:

| IRQ | source | CPU0–3 counts (idle/loaded) | effective_affinity | writable? | notes |

**Exit criterion:** We can answer in writing: *which IRQ serves the Sound Blaster, and can it move?*

---

## Phase 1 — Pi 4 hypothesis on Pi 5 (test, don't assume)

Current player config **copies Pi 4**:

- cmdline: `irqaffinity=0,1 threadirqs`
- units: `CPUAffinity=2-3` (jackd, surge, looper, peak meter); poly governor `0-1`

| # | Question | Method |
|---|----------|--------|
| 1.1 | Does `irqaffinity=0,1` still pile everything on CPU0? | Compare CPU0 vs CPU1 IRQ counts (Pi 4 pattern: GIC targets lowest mask core) |
| 1.2 | Is CPU1 underused for IRQs while CPU0 carries xhci + mmc1? | Phase 0.1 vs 0.2 |
| 1.3 | Does `apply-movable-irq-affinity.sh` do anything on Pi 5? | Run with logging; expect **skip** (Pi 4 IRQ numbers wrong; affinity not writable on 131/162) |
| 1.4 | Audio threads vs display (DSI) contention | Check `rp1-dsi` (IRQ 148) and vc4 threads on cores 2–3 during touch UI |

**Deliverable:** Short verdict: *Pi 4 map preserved / partially preserved / misleading on Pi 5.*

---

## Phase 2 — Affinity levers that might still exist

If `/proc/irq/*/smp_affinity_list` is not writable (early evidence: **no** on 131/162):

| Lever | Pi 5 applicability | Investigate |
|-------|-------------------|-------------|
| `irqaffinity=` cmdline | Still limits default mask | Try **document only** first; any change needs reboot + Suite 0 re-run |
| `CPUAffinity=` per unit | **Works** (already set) | Confirm jackd/surge stay on 2–3 under load (`taskset -pc`) |
| `isolcpus=2,3` + `nohz_full` | Unknown on 2712 | Research + one controlled boot; high risk, late phase |
| `threadirqs` | On cmdline | Confirm threaded handlers for mmc/xhci |
| Kernel `irqbalance` | Off on appliance? | `systemctl is-active irqbalance` |
| Wi‑Fi powersave | **Done** (hygiene) | Re-verify `iw dev wlan0 get power_save` |
| Bluetooth | **Disabled** 2026-08-23 | Confirm no `hci0` traffic |
| USB autosuspend | hygiene sets `power/control=on` | Confirm Sound Blaster port |
| Move Wi‑Fi load | Not Ethernet-only | Measure mmc1 IRQ **with Wi‑Fi associated vs idle** (same SSID, no throughput vs iperf) |

**Do not** use Pi 4 movable IRQ list (41, 42, 43, 28, 57) on Pi 5 — replace with Phase 0 table.

---

## Phase 3 — Candidate configurations (one variable each)

Only after Phase 0–1 are written up. Each candidate gets **one** reference-suite cell (e.g. A @ 1024×3)
× **two runs** for spread — not full Suite 1.

| ID | Hypothesis | Change | Falsifier |
|----|------------|--------|-----------|
| **P5-C0** | Pi 4 copy is fine | *(current)* | Baseline median |
| **P5-C1** | Spread IRQ mask | `irqaffinity=0,1,2,3` or `0-3` | IRQ counts spread; latency same or worse |
| **P5-C2** | Audio on high cores | `irqaffinity=0,1` keep; try `CPUAffinity=2-3` on **peak meter + xrun probe** (currently may float) | Meter/probe PSR stays off 0 |
| **P5-C3** | IRQ cores only 0 | `irqaffinity=0` only (E1 refuted on Pi 4 — **re-test** on Pi 5) | xrun/jitter worse → do not ship |
| **P5-C4** | DSI off during measure | Stop `touch-patch-browser` for cell only | Lower dsi IRQ; use headless measure profile |
| **P5-C5** | Wi‑Fi quiet | Associated but idle vs lightweight ping flood | mmc1 IRQ delta vs xrun |

**Hard rule (from E1):** never change `irqaffinity` **and** `CPUAffinity` in the same experiment.

---

## Phase 4 — Decision and repo promotion

| Outcome | Action |
|---------|--------|
| Pi 4 map validated | Document in `config/platform/pi5.env`; keep player + measurement aligned |
| Pi 4 map wrong but new map wins | New `pi5.env` values + update `boot-assert-cmdline.sh` / unit drop-ins |
| No IRQ levers (all fixed) | **`apply-movable-irq-affinity.sh` Pi 5 no-op** documented; focus USB placement, Wi‑Fi load, `CPUAffinity` only |
| PREEMPT_RT N/A | Already measured — state in every Pi 4 vs Pi 5 comparison |

Promote via **`integrate`** / weekly close — not ad-hoc on player SD.

---

## Immediate actions (2026-08-23)

- [x] Cooler/PSU ordered (human)
- [x] Bluetooth disabled (`systemctl disable --now bluetooth`)
- [x] Wi‑Fi kept; powersave off via `apply-appliance-hygiene.sh`
- [x] **Phase 0 idle capture** → `appliance-state/pi5-irq-census-2026-08-23/` (on Pi)
- [x] Phase 0 **loaded** capture (24 voices × 60 s) — see `pi5-irq-phase1-2026-08-23.md`
- [ ] Cooler + 27 W PSU before Phase 3 latency cells / Suite 1

**Scripts:** `scripts/capture-pi5-irq-census.sh`

---

## Open questions (ranked)

1. **Is RP1 xhci (IRQ 131) unmovable like Pi 4 IRQ 30?** Early: `effective_affinity=0-1`, not writable — likely **stuck in mask**, not free to move to core 1.
2. **Does mmc1 (Wi‑Fi SDIO) share CPU0 with usb1 under load?** Both show CPU0-only counts — **yes**, contention hypothesis.
3. **Does DSI (IRQ 148) belong on audio cores during measure?** Touch UI runs `rp1-dsi` work — Phase 1.4.
4. **Is `threadirqs` + unwritable affinity the whole story on 2712?** May need kernel/doc dive if Phase 2 empty.

---

## References

- Pi 4 E1 refutation: [`e1-three-cores-T1-2026-08-20.md`](archive/e1-three-cores-T1-2026-08-20.md)
- Hygiene: [`Documents/specs/system-hygiene-baseline.md`](../../Documents/specs/system-hygiene-baseline.md)
- Player cmdline: [`PI5-PLAYER-SETUP-LOG.md`](../PI5-PLAYER-SETUP-LOG.md) §C
