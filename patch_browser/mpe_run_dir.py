"""Resolve MPE runtime state directory — mirrors ``mpe_run_dir()`` in audio-engine.sh."""

from __future__ import annotations

import os
from pathlib import Path


def run_dir() -> Path:
    """Return writable runtime dir; fall back to ``${TMPDIR}/mpe`` like bash."""
    configured = Path(os.environ.get("MPE_RUN_DIR", "/run/mpe"))
    if not configured.is_dir():
        try:
            configured.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    if configured.is_dir() and os.access(configured, os.W_OK):
        return configured
    fallback = Path(os.environ.get("TMPDIR", "/tmp")) / "mpe"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
