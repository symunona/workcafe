#!/usr/bin/env python3
"""
detect_ghost_images.py

Finds image rows in scraped.db where file_size > 0 but the file is missing
on disk. Deletes those rows so scrapers re-download them on their next run.

Why delete (not zero file_size): all three scrapers dedup by (cafe_id, photo_id).
A row's mere existence — regardless of file_size — causes the scraper to skip it.
Google scraper also skips a whole cafe if db_count > 0, making ghost rows doubly
harmful.

Usage:
    uv run scripts/detect_ghost_images.py --dry-run
    uv run scripts/detect_ghost_images.py --dry-run --provider naver
    uv run scripts/detect_ghost_images.py
    uv run scripts/detect_ghost_images.py --provider google
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

WDIR = Path(__file__).resolve().parent.parent
DATA = WDIR / "data" / "seoul"
DB_PATH = DATA / "scraped.db"


def local_path_to_disk(local_path: str) -> Path:
    """'/images/naver/123/images/photo.jpg' → data/seoul/naver/123/images/photo.jpg"""
    # Strip leading /images/ prefix — that's the API serving prefix
    rel = local_path.lstrip("/")
    if rel.startswith("images/"):
        rel = rel[len("images/"):]
    return DATA / rel


def main():
    parser = argparse.ArgumentParser(description="Detect and remove ghost image rows")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no deletes")
    parser.add_argument("--provider", choices=["kakao", "naver", "google"], help="Limit to one provider")
    parser.add_argument("--limit", type=int, help="Process at most N rows (for testing)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    where = "file_size > 0 AND local_path IS NOT NULL"
    params: list = []
    if args.provider:
        where += " AND provider = ?"
        params.append(args.provider)

    query = f"SELECT id, cafe_id, provider, local_path, file_size, photo_id FROM images WHERE {where}"
    if args.limit:
        query += f" LIMIT {args.limit}"

    rows = conn.execute(query, params).fetchall()
    print(f"Rows to check (file_size > 0): {len(rows)}")
    if args.dry_run:
        print("DRY RUN — no deletes\n")

    ghost_ids: list[int] = []
    by_provider: dict[str, int] = {}

    for row in rows:
        disk_path = local_path_to_disk(row["local_path"])
        if not disk_path.exists():
            ghost_ids.append(row["id"])
            by_provider[row["provider"]] = by_provider.get(row["provider"], 0) + 1
            if args.dry_run:
                print(f"  GHOST  {row['provider']:8s}  {row['local_path']}")

    print(f"\nGhost rows found: {len(ghost_ids)}")
    for provider, count in sorted(by_provider.items()):
        print(f"  {provider:8s}: {count}")

    if not ghost_ids:
        print("Nothing to do.")
        conn.close()
        return

    if args.dry_run:
        print("\nRun without --dry-run to delete these rows.")
        conn.close()
        return

    # Delete in batches to avoid hitting SQLite variable limit
    batch = 900
    deleted = 0
    for i in range(0, len(ghost_ids), batch):
        chunk = ghost_ids[i : i + batch]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM images WHERE id IN ({placeholders})", chunk)
        deleted += len(chunk)

    conn.commit()
    conn.close()
    print(f"\nDeleted {deleted} ghost rows. Scrapers will re-download on next run.")
    print("Note: Google scraper skips cafes with db_count > 0 — ghost deletion")
    print("only unblocks Google cafes where ALL images were ghosts.")


if __name__ == "__main__":
    main()
