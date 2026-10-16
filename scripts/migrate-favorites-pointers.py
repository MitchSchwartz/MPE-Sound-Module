#!/usr/bin/env python3
"""Migrate favorites from copies to pointers (#181). Run on the device with the browser stopped.

  migrate-favorites-pointers.py dry-run            print the full plan, write nothing
  migrate-favorites-pointers.py run                back up, move files in the library checkout, commit, re-key
  migrate-favorites-pointers.py verify [STAMP]     compare the run's before snapshot with the stores now
  migrate-favorites-pointers.py undo STAMP [--force]
                                                   restore stores and index, reverse the git moves
                                                   (--force: also over settings changed since the run)

run and undo refuse while the touch browser is running (unit or process). A run that began and did not
finish blocks the next run until it is undone. Migrate before the first start on the new code.

Paths come from the environment (MPE_PERSONAL_REPO, MPE_HOME, MPE_SURGE_DOCS, MPE_FAVORITES_NAME,
MPE_FAVORITES_INDEX_FILE and the per-store *_FILE variables), the same ones the browser reads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser import favorites_migrate as fm  # noqa: E402
from patch_browser.library_ops import LibraryError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="personal library checkout (default $MPE_PERSONAL_REPO)")
    p.add_argument("--tuned-folder", default=fm.TUNED_FOLDER)
    p.add_argument("--tuned", action="append", metavar="NAME", help="replace the tuned-patch names (repeatable)")
    p.add_argument("--resave", action="append", metavar="NAME", help="replace the plain re-save names (repeatable)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dry-run")
    sub.add_parser("run")
    v = sub.add_parser("verify")
    v.add_argument("stamp", nargs="?")
    u = sub.add_parser("undo")
    u.add_argument("stamp")
    u.add_argument("--force", action="store_true", help="restore even over settings changed since the run")
    args = p.parse_args(argv)
    try:
        env = fm.environment(args.repo)
        kw = dict(
            repo=env["repo"],
            roots=env["roots"],
            fav_dir=env["fav_dir"],
            index_path=env["index_path"],
            store_paths=env["store_paths"],
            tuned_names=tuple(args.tuned) if args.tuned else fm.TUNED_NAMES,
            resave_names=tuple(args.resave) if args.resave else fm.RESAVE_NAMES,
            tuned_folder=args.tuned_folder,
            last_patch_file=env["last_patch_file"],
        )
        if args.cmd in ("dry-run", "run"):
            plan = fm.build_plan(**kw)
            print(fm.format_plan(plan))
            if args.cmd == "dry-run":
                return 1 if plan.errors else 0
            stamp = fm.apply_plan(plan, repo=env["repo"], index_path=env["index_path"], store_paths=env["store_paths"])
            if stamp is None:
                print("nothing changed")
                return 0
            print(f"\ndone. run: {stamp}")
            print(f"verify: {Path(sys.argv[0]).name} verify {stamp}")
            print(f"undo:   {Path(sys.argv[0]).name} undo {stamp}")
            return 0
        if args.cmd == "verify":
            stamp = args.stamp or fm.latest_run(env["index_path"])
            if not stamp:
                print("no run to verify", file=sys.stderr)
                return 1
            problems = fm.verify(index_path=env["index_path"], store_paths=env["store_paths"], roots=env["roots"], stamp=stamp)
            if problems:
                print("MISMATCH:\n  " + "\n  ".join(problems), file=sys.stderr)
                return 1
            print(f"verify {stamp}: every favorite still has its hits and values")
            return 0
        for line in fm.undo(repo=env["repo"], index_path=env["index_path"], stamp=args.stamp, force=args.force):
            print(line)
        return 0
    except LibraryError as exc:
        print(f"migrate-favorites-pointers: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
