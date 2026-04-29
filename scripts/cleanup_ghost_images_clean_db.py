#!/usr/bin/env python3
"""
Mark images missing from disk as file_size=-1 in clean.db.
Taggers and scrapers filter on file_size > 0, so these are silently skipped.

Safe: no deletes. Reversible by re-scraping + merge-pipeline.

Usage:
    python scripts/cleanup_ghost_images_clean_db.py --dry-run
    python scripts/cleanup_ghost_images_clean_db.py
"""

import argparse, os, sqlite3

DB   = "data/seoul/clean.db"
DATA = "data/seoul"

p = argparse.ArgumentParser()
p.add_argument("--dry-run", action="store_true")
args = p.parse_args()

conn = sqlite3.connect(DB)
rows = conn.execute(
    "SELECT id, local_path FROM images WHERE file_size > 0 AND local_path IS NOT NULL AND local_path != ''"
).fetchall()

missing = []
for img_id, local_path in rows:
    rel  = local_path.removeprefix("/images")
    disk = os.path.join(DATA, rel.lstrip("/"))
    if not os.path.exists(disk):
        missing.append((img_id,))

print(f"Checked : {len(rows):,}")
print(f"Missing : {len(missing):,}  ({100*len(missing)/len(rows):.1f}%)")

if args.dry_run:
    print("DRY RUN — no changes written.")
else:
    conn.executemany("UPDATE images SET file_size = -1 WHERE id = ?", missing)
    conn.commit()
    print(f"Marked {len(missing):,} rows file_size=-1 in {DB}")

conn.close()
