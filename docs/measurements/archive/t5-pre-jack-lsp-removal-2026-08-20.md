# T5-pre — jack_lsp fallback removed (2026-08-20)

**Change:** `read_graph_snapshot()` no longer falls back to `jack_graph()` / `jack_lsp`.
Stale meter → fork-free diagnosis via `/proc` scan for `jackd`.

**Also:** `capture_wedge_diagnostics()` uses `/proc` instead of `pgrep` (T3a clean).

## Pi demonstration

```
T5-pre demo 2026-08-21T04:13:23+01:00
commit: 3949c39

=== meter stopped, jackd running ===
PROBLEM: ... peak-meter stale or unavailable (jackd running — meter fault)
jack_lsp processes before=0 after=0
PASS: no jack_lsp spawned

=== meter stopped, jackd stopped ===
PROBLEM: ... JACK down (jackd not running); ...
jack_lsp processes before=0 after=0
PASS: no jack_lsp spawned
```

Log: `~/t5-pre-demo.log` on Pi.

*Last updated: 2026-08-20 (America/Toronto)*
