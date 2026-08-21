# T5-pre — jack_lsp fallback removed (2026-08-20)

**Change:** `read_graph_snapshot()` no longer falls back to `jack_graph()` / `jack_lsp`.
Stale meter → fork-free diagnosis via `/proc` scan for `jackd`.

**Also:** `capture_wedge_diagnostics()` uses `/proc` instead of `pgrep` (T3a clean).

## Pi demonstration

```
(paste scripts/demo/t5-pre-meter-stale.sh output)
```

*Last updated: 2026-08-20 (America/Toronto)*
