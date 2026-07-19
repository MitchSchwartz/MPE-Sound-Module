# Personal assets (moved)

Patches, Surge binary backup, and user prefs are **not stored in this repo**.

They live in the private **[MPE-Library](https://github.com/M-Ferda/MPE-Library)** repo as `assets/` — clone it as a **sibling** of `MPE-Module`:

```
parent/
├── MPE-Module/      ← this repo (code + docs)
└── MPE-Library/     ← private backup (assets/)
```

Scripts resolve `../MPE-Library` (or legacy `../MPE-Personal`) automatically, or set `MPE_PERSONAL_REPO`. See **[docs/PATHS.md](docs/PATHS.md)**.

Stock Surge factory + third-party patches can also be reinstalled from [Surge XT](https://surge-synthesizer.github.io/) instead of git.
