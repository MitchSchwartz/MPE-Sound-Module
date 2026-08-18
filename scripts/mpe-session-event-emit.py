#!/usr/bin/env python3
"""Emit one session control plane event (bash-safe JSON, validated names)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.session_events import emit_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append one session event line")
    parser.add_argument("event", help="Event name (must be in EVENT_NAMES whitelist)")
    parser.add_argument("detail", nargs="?", default="", help="Optional detail string")
    parser.add_argument("--source", default="", help="Event source label")
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra JSON field (repeatable)",
    )
    parser.add_argument("--run-dir", default=None, help="Override MPE_RUN_DIR")
    args = parser.parse_args(argv)

    fields: dict[str, str] = {}
    for item in args.field:
        if "=" not in item:
            print(f"invalid --field (expected KEY=VALUE): {item}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        fields[key] = value

    run = Path(args.run_dir) if args.run_dir else None
    try:
        emit_event(
            args.event,
            detail=args.detail,
            source=args.source or "bash",
            fields=fields or None,
            run=run,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
