#!/usr/bin/env python3
"""
00_sync_from_scraped.py — Sync new rows from scraped.db into clean.db.

Additive only (INSERT OR IGNORE by primary key). Never modifies scraped.db.
Safe to re-run — idempotent.

Copies:
  - scraped_cafes rows missing from clean.db (new scrapes since last sync)
  - images rows missing from clean.db (new images since last sync)

Run before merge-pipeline so the normalizer sees fresh data.
"""
import argparse
import sqlite3
from pathlib import Path

SCRAPED_DB = "data/seoul/scraped.db"
CLEAN_DB   = "data/seoul/clean.db"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scraped-db", default=SCRAPED_DB)
    p.add_argument("--clean-db",   default=CLEAN_DB)
    args = p.parse_args()

    scraped_path = str(Path(args.scraped_db).resolve())
    clean_path   = str(Path(args.clean_db).resolve())

    conn = sqlite3.connect(clean_path)
    conn.execute(f"ATTACH DATABASE '{scraped_path}' AS src")

    before_cafes  = conn.execute("SELECT COUNT(*) FROM scraped_cafes").fetchone()[0]
    before_images = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]

    print(f"clean.db before:  {before_cafes} scraped_cafes, {before_images} images")

    conn.execute("""
        INSERT OR IGNORE INTO scraped_cafes
            (id, provider, provider_id, name, lat, lon, address, url, metadata,
             scraped_at, belongs_to_cafe_id, name_embedding, llm_english, metadata_last_checked)
        SELECT
            id, provider, provider_id, name, lat, lon, address, url, metadata,
            scraped_at, belongs_to_cafe_id, name_embedding, llm_english, metadata_last_checked
        FROM src.scraped_cafes
    """)

    conn.execute("""
        INSERT OR IGNORE INTO images
            (id, cafe_id, provider, local_path, image_url, gallery_url, photo_id,
             photo_type, tags, registered_at, width, height, file_size,
             exif_date, exif_lat, exif_lon, scraped_at, belongs_to_cafe_id)
        SELECT
            id, cafe_id, provider, local_path, image_url, gallery_url, photo_id,
            photo_type, tags, registered_at, width, height, file_size,
            exif_date, exif_lat, exif_lon, scraped_at, belongs_to_cafe_id
        FROM src.images
    """)

    conn.commit()

    after_cafes  = conn.execute("SELECT COUNT(*) FROM scraped_cafes").fetchone()[0]
    after_images = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]

    new_cafes  = after_cafes  - before_cafes
    new_images = after_images - before_images

    print(f"clean.db after:   {after_cafes} scraped_cafes (+{new_cafes}), {after_images} images (+{new_images})")

    unprocessed = conn.execute(
        "SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NULL"
    ).fetchone()[0]
    print(f"Unprocessed scraped_cafes (ready for normalize): {unprocessed}")

    conn.execute("DETACH DATABASE src")
    conn.close()


if __name__ == "__main__":
    main()
