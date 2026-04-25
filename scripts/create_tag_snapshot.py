#!/usr/bin/env python3
"""
Create a tagging-experiment snapshot DB from clean.db.

Copies top N clean cafes (by image count) with their scraped_cafes and images
into a fresh history DB. clean.db is never modified.

Prints the output path on the last line (for shell capture).

Usage:
    python scripts/create_tag_snapshot.py --n 100 --threshold 0.25
    python scripts/create_tag_snapshot.py --n all --threshold 0.27 --output data/seoul/history/clean_custom.db
"""

import argparse, os, sqlite3
from datetime import date
from pathlib import Path

SOURCE  = "data/seoul/clean.db"
HISTORY = "data/seoul/history"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n",         default="100",  help="Number of clean cafes (int or 'all')")
    p.add_argument("--threshold", type=float, default=0.22, help="Threshold label for filename")
    p.add_argument("--from-db",   default=SOURCE, dest="source", help="Source clean.db path")
    p.add_argument("--output",    default="",     help="Override output path")
    args = p.parse_args()

    n_all = args.n == "all"
    n_int = None if n_all else int(args.n)
    today = date.today().isoformat()
    t_str = f"t{int(args.threshold * 100):02d}"
    n_str = "all" if n_all else str(n_int)

    out_path = args.output or f"{HISTORY}/clean_tags_{t_str}_n{n_str}_{today}.db"
    Path(HISTORY).mkdir(parents=True, exist_ok=True)

    if os.path.exists(out_path):
        # Validate it's not an empty/broken leftover
        try:
            n_check = sqlite3.connect(out_path).execute("SELECT COUNT(*) FROM images").fetchone()[0]
        except Exception:
            n_check = 0
        if n_check > 0:
            print(f"Already exists ({n_check} images), skipping copy: {out_path}", flush=True)
            print(out_path)
            return
        print(f"Existing file empty or broken, recreating: {out_path}", flush=True)
        os.remove(out_path)

    print(f"Source:  {args.source}", flush=True)
    print(f"Output:  {out_path}", flush=True)
    print(f"Cafes:   {n_str}  Threshold label: {args.threshold}", flush=True)

    dst = sqlite3.connect(out_path)
    abs_source = str(Path(args.source).resolve())
    dst.execute(f"ATTACH DATABASE '{abs_source}' AS src")

    dst.executescript("""
        CREATE TABLE IF NOT EXISTS clean_cafes (
            id TEXT PRIMARY KEY, chain_id TEXT, name TEXT, english_name TEXT,
            avg_lat REAL, avg_lon REAL, address TEXT, url TEXT,
            providers TEXT, source_ids TEXT, metadata TEXT, tags TEXT
        );
        CREATE TABLE IF NOT EXISTS cafe_chains (
            id TEXT PRIMARY KEY, name TEXT, name_english TEXT
        );
        CREATE TABLE IF NOT EXISTS scraped_cafes (
            id TEXT PRIMARY KEY, provider TEXT, provider_id TEXT, name TEXT,
            lat REAL, lon REAL, address TEXT, url TEXT, metadata TEXT,
            belongs_to_cafe_id TEXT, scraped_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scraped_belongs ON scraped_cafes(belongs_to_cafe_id);
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY,
            cafe_id TEXT, provider TEXT, local_path TEXT, image_url TEXT,
            photo_id TEXT, file_size INTEGER, belongs_to_cafe_id TEXT,
            width INTEGER, height INTEGER, scraped_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_images_cafe_id    ON images(cafe_id);
        CREATE INDEX IF NOT EXISTS idx_images_belongs    ON images(belongs_to_cafe_id);
        CREATE TABLE IF NOT EXISTS image_tags (
            image_id INTEGER NOT NULL REFERENCES images(id),
            tag      TEXT    NOT NULL,
            score    REAL    NOT NULL DEFAULT 1.0,
            boxes    TEXT,
            PRIMARY KEY (image_id, tag)
        );
        CREATE INDEX IF NOT EXISTS idx_image_tags_tag   ON image_tags(tag);
        CREATE INDEX IF NOT EXISTS idx_image_tags_image ON image_tags(image_id);
    """)

    limit_clause = "" if n_all else f"LIMIT {n_int}"

    # Top N clean cafes by image count
    dst.execute(f"""
        INSERT INTO clean_cafes
            (id, chain_id, name, english_name, avg_lat, avg_lon,
             address, url, providers, source_ids, metadata)
        SELECT cc.id, cc.chain_id, cc.name, cc.english_name, cc.avg_lat, cc.avg_lon,
               cc.address, cc.url, cc.providers, cc.source_ids, cc.metadata
        FROM src.clean_cafes cc
        JOIN (
            SELECT sc.belongs_to_cafe_id, COUNT(i.id) AS img_count
            FROM src.scraped_cafes sc
            JOIN src.images i ON i.cafe_id = sc.id
            WHERE i.file_size > 0 AND i.local_path IS NOT NULL AND i.local_path != ''
              AND sc.belongs_to_cafe_id IS NOT NULL
            GROUP BY sc.belongs_to_cafe_id
            ORDER BY img_count DESC
            {limit_clause}
        ) top ON cc.id = top.belongs_to_cafe_id
    """)
    n_clean = dst.execute("SELECT COUNT(*) FROM clean_cafes").fetchone()[0]
    print(f"  clean_cafes copied:   {n_clean}", flush=True)

    # Chains referenced by those clean cafes
    try:
        dst.execute("""
            INSERT INTO cafe_chains (id, name, name_english)
            SELECT ch.id, ch.name, ch.name_english
            FROM src.cafe_chains ch
            WHERE ch.id IN (SELECT chain_id FROM clean_cafes WHERE chain_id IS NOT NULL)
        """)
        n_chains = dst.execute("SELECT COUNT(*) FROM cafe_chains").fetchone()[0]
        print(f"  cafe_chains copied:   {n_chains}", flush=True)
    except Exception as e:
        print(f"  cafe_chains skipped:  {e}", flush=True)

    # Scraped cafes belonging to those clean cafes
    dst.execute("""
        INSERT INTO scraped_cafes
            (id, provider, provider_id, name, lat, lon, address, url, metadata, belongs_to_cafe_id, scraped_at)
        SELECT sc.id, sc.provider, sc.provider_id, sc.name, sc.lat, sc.lon,
               sc.address, sc.url, sc.metadata, sc.belongs_to_cafe_id,
               COALESCE(sc.scraped_at, '')
        FROM src.scraped_cafes sc
        WHERE sc.belongs_to_cafe_id IN (SELECT id FROM clean_cafes)
    """)
    n_scraped = dst.execute("SELECT COUNT(*) FROM scraped_cafes").fetchone()[0]
    print(f"  scraped_cafes copied: {n_scraped}", flush=True)

    # Images for those scraped cafes (valid files only)
    dst.execute("""
        INSERT INTO images
            (id, cafe_id, provider, local_path, image_url, photo_id, file_size,
             belongs_to_cafe_id, width, height, scraped_at)
        SELECT i.id, i.cafe_id, i.provider, i.local_path, i.image_url,
               i.photo_id, i.file_size, i.belongs_to_cafe_id,
               COALESCE(i.width, 0), COALESCE(i.height, 0), COALESCE(i.scraped_at, '')
        FROM src.images i
        WHERE i.cafe_id IN (SELECT id FROM scraped_cafes)
          AND i.file_size > 0 AND i.local_path IS NOT NULL AND i.local_path != ''
    """)
    n_images = dst.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    print(f"  images copied:        {n_images}", flush=True)

    dst.commit()
    dst.execute("DETACH DATABASE src")
    dst.execute("PRAGMA journal_mode=WAL")
    dst.close()

    # Write .md notes (picked up by the snapshot browser)
    md = f"""Snapshot: **{n_clean} cafes**, {n_images} images — {today}

- Source: `{args.source}`
- Scraped cafes included: {n_scraped}
- Created: {today}

Re-tag: `just tag-images-yolo {n_str} 0.25`
"""
    Path(out_path.replace(".db", ".md")).write_text(md)

    print(f"\nSnapshot ready: {out_path}", flush=True)
    print(out_path)   # last line — captured by shell


if __name__ == "__main__":
    main()
