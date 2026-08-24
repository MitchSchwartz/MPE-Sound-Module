# G3 — pull the raw measurement logs off the appliance and archive them usefully

**Gap G3 from OM-Repo [`SRED-EVIDENCE-2026.md`](../../../OM-Repo/internal/projects/mpe-synth-launch/sred/SRED-EVIDENCE-2026.md).** Every number in
74 measurement documents traces back to a log file that exists in exactly one place: the SD
card in `raspberrypi2`. That card shares IRQ 41 with the SDIO WiFi, gets hammered by every
build, and has no backup. If it fails, the documents survive and **the evidence behind them
does not.**

This is an archival task, not a measurement. Nothing here changes appliance behaviour.

---

## HARD PRECONDITION — the Pi must be idle

**Run no command against `raspberrypi2` until you have established the box is free.** No
soak, no ladder, no measurement window in flight. Establish it from what Mitch has said or
from a log already pulled — **do not poll the Pi to find out.**

This task copies a large number of files. It is exactly the kind of I/O that invalidates a
run in flight, and an 8-hour soak killed at hour 6 costs far more than waiting.

**If a run is in flight: stop and say so.** This task keeps.

---

## What you are looking for

Referenced log paths, extracted from the docs. Four distinct locations, and they do not all
have the same survival odds:

| location | examples | expectation |
|---|---|---|
| `~/` (i.e. `/home/mitch`) | `latency-measure.log`, `t11-condA.log`, `t13-condA.log`, `t5-soak.log`, `instrument-soak-1024x2.log`, `i3-n15.log`, `256-A-streams.log` | **Most likely intact. Highest priority.** |
| dated plan dirs | `/home/mitch/plan-v8-20260821-225953/`, `/root/plan-v7-20260821-223340/`, `/root/plan-v-20260821-221011/` | Intact; note two are under `/root` and need `sudo` to read |
| `/tmp/` | `sooperlooper.log`, `lat.txt`, `t4-loop-curve.log`, `step0-load.log`, `d15-512x3-*.log`, `latency-midi-load-*.log` | **Probably already gone** — `/tmp` does not survive reboot, and the appliance has rebooted many times since 08-18. Record what is missing; do not treat absence as an error. |
| harness default | `$MPE_MODULE_REPO/logs` (`LOG_DIR`), `$HOME/latency-measure.log` (`MPE_LATENCY_LOG`) | Check both; `measure-latency-run.sh:33` writes here by default |

**Do not work only from the list above.** Sweep for anything matching the measurement
naming conventions — `latency-*`, `plan-*`, `t[0-9]*`, `v[0-9]*`, `w1-*`, `i3-*`,
`scarlett-*`, `step[0-9]*`, `*soak*`, `*-condA*`, `*.log`, `*.jsonl` — in `/home/mitch`,
`/root`, and `/tmp`. The docs cite what was interesting; there will be more on disk.

---

## Steps

**1. Inventory before copying.** Produce a manifest first: path, size, mtime, line count.
Sum the total. **Report the total size before transferring anything** — if it is large
enough to matter for the repo, that is a decision for Mitch, not for you.

**2. Copy, preserving mtime.** `rsync -a` or `scp -p`. **Modification times are evidence** —
they are the independent corroboration of the chronology in the SR&ED record, and a copy
that stamps everything with today's date destroys exactly the property that makes the
archive worth having. Verify mtimes survived the transfer.

**3. Land them in a structure that stays legible.** Proposed:

```
docs/measurements/raw-logs/
  MANIFEST.md            <- path, size, mtime, sha256, and which doc cites it
  home/                  <- mirrors /home/mitch
  root/                  <- mirrors /root  (note: was root-owned on the Pi)
  tmp/                   <- whatever survived
```

Mirror the original directory layout rather than flattening. A flat directory loses the
information that `plan-v7-.../v3-1024x2.log` and `plan-v-.../v1-silence.log` came from
different orchestrated runs, and several base names collide across directories.

**4. Write `MANIFEST.md`.** One row per file: original absolute path, size, mtime, `sha256`,
and — where you can establish it by grepping `docs/measurements/` — **which document cites
it.** That last column is what turns a pile of logs into evidence. Files nothing cites still
get archived; mark them `(uncited)`.

**5. Record what is missing.** A `## Not found` section listing every path cited in the docs
that no longer exists on disk, with the doc that cites it. **This is a required output, not
a footnote.** "We looked and it was gone" is a real finding; silence reads as "we never
checked", and one of this project's standing rules is that a reading which looks the same
whether the instrument worked or was blind cannot be trusted.

**6. Check for secrets before committing.** These are bench logs and should contain nothing
sensitive, but they were machine-generated and never reviewed by a human. Scan for hostnames
beyond `raspberrypi2`, any WiFi/SSID material, tokens, and absolute paths that leak anything
private. `gitleaks` runs on commit here — do not rely on it as the only check.

**7. Size check before commit.** If the total is more than a few tens of MB, **stop and ask
Mitch** rather than committing. Options to put to him: compress per-directory (`.tar.zst`,
keeping `MANIFEST.md` plaintext so it stays greppable and diffable), git-lfs, or an
out-of-repo archive with the manifest committed. Do not silently truncate, sample, or drop
files to fit — if anything is excluded, say which and why.

---

## Constraints

- **One connection, batched.** Inventory in a single pass, transfer in a single pass. Do not
  issue dozens of small SSH commands — see the standing rule on Pi contact.
- **Read-only on the appliance.** Copy files off. **Do not delete, move, rotate, or tidy
  anything on the Pi**, however redundant it looks. Freeing disk is not this task.
- `/root` paths need `sudo` to read. Copy them out as readable files; do not chase
  permissions on the source.
- **Do not edit, reformat, normalise, or "clean up" log contents.** Byte-identical or it is
  not evidence. Record the `sha256` so that stays checkable.
- If a file is unreadable or a transfer fails, **report it** — do not skip it silently.

---

## Hand back

Total bytes, file count, the manifest path, the `## Not found` list, whether it was
committed or is awaiting a size decision, and anything on the Pi that looked like a
measurement artifact but did not match the conventions above.
