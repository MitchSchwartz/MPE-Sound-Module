"""What code is this process actually running?

2026-08-27: `looper-session.py` ran from 21:48 one evening to 08:20 the next
morning while eleven commits landed. Every deploy in between printed success
and the new SHA — `bench: repo at 691a13d` — because it read the *checkout*.
The process had imported the old modules at startup and never looked again, so
the pads kept behaving like yesterday's build. Hours went into a track-15 bug
that was really a stale process.

`bench: repo at <sha>` was a reading that looked identical whether the running
code was current or a day old, which is the defect shape this appliance keeps
producing. The fix is to report something that can actually differ:

  * the SHA of the repo the *imported modules* came from, resolved through this
    module's own ``__file__`` rather than the working directory, and
  * whether any loaded source file has been modified since the process started
    — which is exactly the stale-process condition, and is detectable without
    knowing anything about git.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Process start, captured at import. Files newer than this were changed after
# this process read them into memory.
_IMPORTED_AT = time.time()

_MODULE_DIR = Path(__file__).resolve().parent


def repo_sha(start: Path | None = None) -> str:
    """Short SHA of the checkout the loaded modules came from.

    Resolved from this file's path, not the cwd — a bench started from the home
    directory would otherwise report whatever repo it happened to be sitting in.
    """
    where = (start or _MODULE_DIR).resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(where), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else "unknown"


def stale_source_files(*, since: float | None = None) -> list[str]:
    """Loaded .py files modified after this process imported them.

    A non-empty list means the process is running code that no longer matches
    the disk — i.e. a deploy landed and nothing restarted this process.
    """
    cutoff = _IMPORTED_AT if since is None else since
    stale: list[str] = []
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path or not path.endswith(".py"):
            continue
        try:
            if os.stat(path).st_mtime > cutoff + 1.0:
                stale.append(path)
        except OSError:
            continue
    return sorted(stale)


def running_code_sha() -> str:
    """One line for a startup banner: the SHA, and any staleness.

    At startup nothing is stale by definition, so this normally reports the SHA
    alone. Call it again later — from a health check — and it will say so when
    a deploy has landed underneath a long-lived process.
    """
    sha = repo_sha()
    stale = stale_source_files()
    if not stale:
        return f"{sha} (modules loaded from {_MODULE_DIR})"
    names = ", ".join(Path(p).name for p in stale[:3])
    more = f" +{len(stale) - 3} more" if len(stale) > 3 else ""
    return (
        f"{sha} — STALE: {len(stale)} loaded file(s) changed on disk since "
        f"startup ({names}{more}). This process is NOT running the deployed "
        f"code; restart mpe-looper-session.service."
    )
