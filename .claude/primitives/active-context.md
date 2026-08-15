# Active context — MPE-Module

*Last updated: 2026-08-15 16:08 (America/Toronto)*

## Current phase

| Field | Value |
|---|---|
| **Product** | Raspberry Pi MPE sound module (Surge XT + patch browser) |
| **Phase** | Phase 2 looper eval / YOLO lane bootstrap |
| **Integration branch** | `dev` |
| **Nerdrack runner** | Claude Code (`scripts/yolo/claude-yolo.sh`) |
| **Pi soak** | Laptop / Mitch only — Pi is LAN-only, not reachable from nerdrack |

## Queued next

| Queue id | Status | Blocked by |
|---|---|---|
| *(empty)* | — | — |

## Notes

- Nerdrack runs **unit tests only** (`python3 -m unittest discover -s tests -q`).
- Appliance deploy, audio profile, systemd, and `mpe restart` stay Mitch gates.
