# Review loop — cycle 2 fixes (2026-08-22)

Applied after grumpy + audit cycle 2 (5 P1):

| P1 | Fix |
|---|---|
| Spam guard unreachable at 0.15 s poll | `spam_threshold_per_s()` = `max(2, int(1/poll)-1)`; journal takes poll interval |
| SIGTERM never reaches stop() | Daemon uses `_Stop` handler + 0.2 s slice sleep (matches snapshot publisher) |
| send_polylimit failure invisible | `log_send_failed` once per (old,new,reason) until success |
| enable/disable flip silent | `log_enabled_change` on `_enabled` transition |
| test_startup_log_once thread leak | `addCleanup(governor.stop)` + patched state file |

Also: verbose trace written before suppress check; startup line includes `warm_window` and `emergency_poly`.

Tests: 16/16 `tests.test_surge_poly_governor`.
