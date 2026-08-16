"""Test path setup — bench modules use bare imports (apc_grid, sl_loop_states)."""

from __future__ import annotations

import sys
from pathlib import Path

_SOOPERLOOPER = Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"
if str(_SOOPERLOOPER) not in sys.path:
    sys.path.insert(0, str(_SOOPERLOOPER))
