# Step 0 — restore JACK to 1024×3 (2026-08-21)

**Pi:** raspberrypi2 · **Before:** T13 left jackd at `-p 256 -n 3` (jack_bufsize read **256**).

## Action

```bash
sudo ./scripts/set-surge-audio.sh --buffer 1024 --periods 3
```

## Trap 5 readback (from JACK, not the argument passed)

| check | value |
|---|---|
| `jack_bufsize` | **1024** |
| `ps` jackd cmdline | `-p 1024 -n 3` |

**Verdict:** shipping buffer restored before Step 1 or Phase 0 changes.
