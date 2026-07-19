# Personal assets (moved)

Patches, Surge binary backup, and user prefs are **not stored in this repo**.

Use a **private git repo** (your choice of name) as a sibling of `MPE-Module`, with an `assets/` tree:

```
parent/
├── MPE-Module/      ← this repo (code + docs)
└── mpe-assets/      ← your private backup (assets/)
    └── assets/
        ├── user-data/Patches/
        ├── patches/
        └── binaries/
```

Scripts resolve `../mpe-assets`, `../MPE-Library`, or `../MPE-Personal` automatically, or set `MPE_PERSONAL_REPO`. See **[docs/PATHS.md](docs/PATHS.md)**.

Stock Surge factory + third-party patches can also come from a [Surge XT](https://surge-synthesizer.github.io/) install or from building Surge on the Pi — no separate assets repo required for the bundled library.
