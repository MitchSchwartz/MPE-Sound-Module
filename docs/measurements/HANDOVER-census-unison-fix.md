# Handover — `unison_voices` is fabricated; fix the parser, then amend P7

**Two tasks.** Task 1 is a correctness fix to a measurement instrument whose output is
already being cited in decisions. Task 2 is a five-minute addition to the P7 prompt.

Do **not** touch `unison_voices` conclusions until Task 1 lands — the current value would
resurrect a theory that was explicitly retracted in
`PATCH-COST-what-makes-them-heavy.md`.

---

## Task 1 — `scripts/parse-fxp-metadata.py`, `unison_voices` field

### The evidence (already verified against raw XML, do not re-derive)

| patch | census emitted | what the file actually contains |
|---|---|---|
| Crystals | `unison_voices: 14` | `osc{1,2,3}_param0` = **4, 4, 6** — three Plaits **engine indices**, summed. `param6` = 0. Real unison: **1** |
| Cloud Horn | `16` | `osc{1,2}_param0` = **8, 8** — two String **mode selectors**. Real unison: **1** |
| Brave New World | `3` | `osc{1,2,3}_param6` = **1, 1, 1** — three oscillators each at unison **1**. Not "unison 3" |
| Duduk | `1` | correct (one unmuted Wavetable, `param6` = 1) |

Reproduce with:

```bash
python3 - <<'PY'
import re
base='/home/mitch/Documents/GitHub/MPE-Library/assets/user-data/quick-select/latest/Quick Select/'
for name in ('Crystals','Cloud Horn','Brave New World','Duduk'):
    raw=open(base+name+'.fxp','rb').read()
    xml=raw[raw.find(b'<?xml'):raw.find(b'</patch>')]
    print(name)
    for n in (1,2,3):
        g=lambda k: (lambda m: m.group(1).decode() if m else None)(
            re.search((r'<a_osc%d_%s\b[^>]*value="([^"]*)"'%(n,k)).encode(), xml))
        print('  osc%d type=%s param0=%s param6=%s mute=%s' % (
            n, g('type'), g('param0'), g('param6'),
            (lambda m: m.group(1).decode() if m else None)(
                re.search((r'<a_mute_o%d\b[^>]*value="([^"]*)"'%n).encode(), xml))))
PY
```

### Two independent bugs

**1. `_UNISON_PARAM0_TYPES` (line 21) is backwards for types 9 and 10.**

```python
_UNISON_PARAM0_TYPES = frozenset({8, 9, 10, 11, 12, 13, 14, 15})
```

For **Twist/Plaits (10)** and **String (9)**, `param0` is the **engine / mode selector**, and
those engines have **no unison at all**. The comment above the line — *"param0 is typically
unison voice count"* — encodes a guess as a fact. This is the exact misreading that
`PATCH-COST-what-makes-them-heavy.md` retracted; the parser reintroduced it.

Verify each remaining type in that set (8 Modern, 11 Alias, 12-15) against real patch files
before trusting any of them. Do not carry over the assumption for the ones you did not check
— drop them from the set and record which are unverified.

**2. It sums across oscillators** (`unison_voices += ...`). Unison is per-oscillator and
multiplies with oscillator count; a summed scalar is not a meaningful quantity even where
`param0` is the right field.

### Sanity check that catches this class of bug

If Cloud Horn genuinely ran 16 unison voices across two String models it would be the
heaviest patch in the library by a wide margin. It is mid-tier — confirm-verified clean at 5.
**A reported cost driver that contradicts measured cost means the field is wrong.** Add a
check of this kind to `tests/test_parse_fxp_metadata.py`: assert the fixed values above,
so this cannot silently regress.

### The fix

- Types **9** and **10**: unison is always **1**. Emit `param0` as a separate
  `osc_engines` field — it is genuinely useful (Crystals = engines 4/4/6, Cloud Horn = 8/8)
  and belongs in the census as its own column.
- Types with real unison: read the dedicated per-oscillator param and emit a **list**, never
  a sum: `"unison_per_osc": [1, 1, 1]`.
- Re-run `scripts/run-quick-select-census.sh`, regenerate the 53-patch JSONL, and
  **correct the unison column wherever the census has been quoted** — session handoff,
  V9 review, DECISIONS if it appears there.

### What is NOT affected — do not re-do this

`osc_types`, `osc_count`, mute handling, and every filter figure are independent of unison.
The gate answer stands as reported: **triple Plaits is 1/53, any Twist is 2/53 — a rare edge
case.** Only the unison column is poisoned.

---

## Task 2 — add Duduk @ 3 to `PROMPT-P7-overclock-diagnostic.md`

The census surfaced the more useful finding: **`filter1 >= 10` on 12/53 patches (23%) —
expensive filters are ~5x more common than exotic oscillators.** Duduk is the proof case:
**1 unmuted Wavetable oscillator, filters 11/20, still floor-3 class.** Oscillator count does
not predict cost on this library; filter choice does more of the work.

P7 currently measures Crystals @ 3 and Cloud Horn @ 5 — **both oscillator-dominated.** Add:

- **Duduk @ 3** (confirm-verified clean at 3 in V9-d)

Costs ~5 minutes. It tests whether **filter-bound and oscillator-bound work respond to clock
the same way.** If they scale together, the appliance is uniformly compute-bound and the
result generalises. **If they diverge, that is a more valuable finding than the 11%** — it
would mean one of the two cost centres is bound by something other than clock, which changes
what P8 and the multithreading assessment are worth.

Same rules as the rest of P7: confirm harness only, `dsp_p99`/`dsp_max` at fixed voice count,
verify achieved clock with `vcgencmd measure_clock arm` and `get_throttled` before and after
every window, revert to stock before tonight's soak.
