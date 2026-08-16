# SooperLooper test loops (16)

Stereo 48 kHz WAV clips for automated smoke tests — **not** committed (generate locally).

```bash
bash scripts/sooperlooper/generate-test-clips.sh
# → loop00.wav … loop15.wav (distinct sine tones, 2 s default)
```

Smoke test:

```bash
bash scripts/sooperlooper/smoke-16-loops.sh
```

Uses `load_loop` OSC (works on eval Pi); does not depend on `save_loop` (B8 fail).
