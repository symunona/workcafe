#!/usr/bin/env python3
"""
data_2_merge_files.py

For each image dir referenced in scraped.db:
  - If the dir exists in data/ as a real directory → skip (already there)
  - If it's a symlink in data/ → remove symlink, move real dir from data-2/, delete from data-2
  - If it's missing in data/ but exists in data-2/ → move dir to data/, delete from data-2
  - If missing in both → report (no data loss, nothing to do)

Run with --dry-run first to preview.

Usage:
    uv run scripts/data_2_merge_files.py --dry-run
    uv run scripts/data_2_merge_files.py --dry-run --limit 20
    uv run scripts/data_2_merge_files.py
"""

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

WDIR = Path(__file__).resolve().parent.parent
DATA = WDIR / "data" / "seoul"
DATA2 = WDIR / "data-2" / "seoul"
DB_PATH = DATA / "scraped.db"


def local_path_to_rel_dir(local_path: str) -> str | None:
    """'/images/naver/37411185/images/photo.jpg' → 'naver/37411185'"""
    parts = local_path.lstrip("/").split("/")
    # [0]=images [1]=provider [2]=source_id [3]=images [4]=photo.jpg
    if len(parts) >= 3 and parts[0] == "images":
        return f"{parts[1]}/{parts[2]}"
    return None


def main():
    parser = argparse.ArgumentParser(description="Merge data-2 image dirs into data")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N dirs (for testing)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT DISTINCT local_path FROM images WHERE local_path IS NOT NULL AND file_size > 0"
    ).fetchall()
    conn.close()

    rel_dirs: set[str] = set()
    for (lp,) in rows:
        rel = local_path_to_rel_dir(lp)
        if rel:
            rel_dirs.add(rel)

    print(f"Unique image dirs in DB: {len(rel_dirs)}")
    if args.dry_run:
        print("DRY RUN — no changes will be made\n")

    dirs = sorted(rel_dirs)
    if args.limit:
        dirs = dirs[: args.limit]
        print(f"(limited to {args.limit} dirs)\n")

    counts = {"moved": 0, "symlink_replaced": 0, "skipped": 0, "not_in_data2": 0}

    for rel in dirs:
        target = DATA / rel
        source = DATA2 / rel

        is_symlink = target.is_symlink()
        is_real_dir = target.is_dir() and not is_symlink

        if is_real_dir:
            counts["skipped"] += 1
            continue

        # Not a real dir in data/ — check data-2/
        if not source.is_dir():
            counts["not_in_data2"] += 1
            continue

        if is_symlink:
            action = "replace symlink"
            counts["symlink_replaced"] += 1
        else:
            action = "move"
            counts["moved"] += 1

        if args.dry_run:
            print(f"  [{action}] {rel}  ({source} → {target})")
        else:
            if is_symlink:
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))

    print(f"\nResults:")
    print(f"  Moved from data-2:       {counts['moved']}")
    print(f"  Symlinks replaced+moved: {counts['symlink_replaced']}")
    print(f"  Already in data (skip):  {counts['skipped']}")
    print(f"  Not found in data-2:     {counts['not_in_data2']}")

    if not args.dry_run:
        total_acted = counts["moved"] + counts["symlink_replaced"]
        print(f"\n{total_acted} dirs moved. Verify with --dry-run (should show 0 moves now).")


if __name__ == "__main__":
    main()
