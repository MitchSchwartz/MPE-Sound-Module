# MPE-Module — agent orientation

*Last updated: 2026-08-02 (America/Toronto)*

**Product:** Raspberry Pi MPE sound module (Surge XT headless + patch browser UI).

---

## Git workflow (read first)

**Canonical doc:** [`docs/GIT-WORKFLOW.md`](docs/GIT-WORKFLOW.md)

Hard rules for agents:

1. **Feature work → `dev`.** Do not merge to **`main`** until Mitch confirms Pi soak on `dev` (or explicitly says "merge to main" / "promote").
2. **Never push to `dev` and `main` in the same testing pass.** That bypasses integration testing and confuses what the Pi is running.
3. **Test on the Pi by checking out the branch** (`dev`, `yolo/*`, or PR head) — not by promoting to `main` first.
4. **Stable appliance:** Pi clone on **`main`**. After promotion, switch the Pi back to `main` and run `configure-pi-paths.sh --local --force`.
5. **Appliance env** (`/etc/mpe/mpe.env`) persists across branch switches — audio profile, UI mode, etc. are not wiped by git checkout.

---

## Pi deploy

| Action | Command (on Pi) |
|--------|------------------|
| Apply branch | `git fetch && git checkout <branch> && git pull && ./scripts/configure-pi-paths.sh --local --force` |
| Remote from PC | `scripts/configure-pi-paths.sh [--force]` (uses `PI_HOST` from `config/mpe.env`) |

Repo path on Pi: `~/MPE-Module` (override via `MPE_MODULE_REPO` in `/etc/mpe/mpe.env`).

---

## Key docs

| Topic | Doc |
|-------|-----|
| Git branches + Pi testing | [`docs/GIT-WORKFLOW.md`](docs/GIT-WORKFLOW.md) |
| Paths / env vars | [`docs/PATHS.md`](docs/PATHS.md) |
| USB desk tether (`usb-host`) | [`docs/USB-AUDIO-HOST.md`](docs/USB-AUDIO-HOST.md) |
| USB session record (`usb-host-session`) | [`docs/USB-SESSION-RECORD.md`](docs/USB-SESSION-RECORD.md) |
| Touch UI demo screen record | [`docs/TOUCH_PATCH_BROWSER.md`](docs/TOUCH_PATCH_BROWSER.md) · `scripts/record-screen.sh` |
| Touch UI | [`docs/TOUCH_PATCH_BROWSER.md`](docs/TOUCH_PATCH_BROWSER.md) |
| Boot recovery | [`docs/PI-BOOT-RECOVERY.md`](docs/PI-BOOT-RECOVERY.md) |

---

## Tests

```bash
python3 -m unittest discover -s tests -q
```

Run before opening PRs to `dev`.
