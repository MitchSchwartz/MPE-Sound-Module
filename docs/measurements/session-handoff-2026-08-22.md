# Session handoff — V8/V9 patch capacity (2026-08-22)

**Branch:** `docs/v8-patch-capacity` @ `be3f2e7` (open PR to `dev` pending)  
**Pi state:** `1024×2`, softmode **1**, poly governor **OFF** (measurement), jackd card **2**

## Done this session

| cell | result |
|---|---|
| **V8-a** | 53 Quick Select patches @ 1024×3 ramp — see [`v8-patch-capacity-2026-08-21.md`](v8-patch-capacity-2026-08-21.md) |
| **V8-b** | Cloud Horn @ 7 overload; ×2 vs ×3 no separation @ ~80% DSP |
| **V9-a** | Crystals/Cloud Horn hold @ ramp count over 60 s; Closed Hat fails |
| **V9-b** | **1024×2 clean** @ Cloud Horn 5 voices (0 xruns ×3 runs both configs) |
| **V9-c1** | Cloud Horn @ 7 × 60 s → **40 xruns** (V8-b regression, not duration) |
| **V9-c2** | Closed Hat confirm ceiling **5** @ 60 s (not 15) |
| **V9-c3** | Crystals @ 512×3 ramp: clean **3**, overrun @ 5 |
| **V9-d** | Duduk + Brave New World @ 3: ×2 and ×3 all **0 xruns** |

**Canon:** [`V9-REVIEW-2026-08-22.md`](V9-REVIEW-2026-08-22.md)

## Decisions ready (Mitch gates)

1. **Ship `1024×2` as instrument default** — measured free at clean load (64→42.7 ms latency). Looper stack still documented @ 1024×3 condition D.
2. **Poly governor floors** — use **confirm** counts, not V8-a ramp alone: Crystals **3**, Cloud Horn **5**, etc.
3. **Do not use V8-a ≥15 rows** without `measure-confirm-at-voices.sh`.

## Blocked on Mitch

- Re-enable `surge-poly-governor.service` + ceiling values in `/etc/mpe/mpe.env`
- Product call: instrument-only ×2 vs looper ×3 split in shipped profile
- Percussive capacity metric (Kick, Closed Hat) — design in V9-REVIEW §V10-c

## Agent next (no gate)

- **V10-b:** Fix `measure-capacity-ramp.sh` `_xruns_delta` to match confirm harness counting
- Merge `docs/v8-patch-capacity` → `dev` after PR review

## Pi artifact dirs

```
~/plan-v8-20260821-225953/
~/plan-v9a-20260822-030357/
~/plan-v9b-20260822-031604/
~/plan-v9c-20260822-033058/
~/plan-v9c-redo-20260822-034337/   # ceiling search + Closed Hat 15×8 diagnostic
~/plan-v9d-20260822-035513/        # Duduk (BNW failed duplicate-tag; re-run below)
~/plan-v9d-bnw-20260822/           # Brave New World complete
```

*Last updated: 2026-08-22 (America/Toronto)*
