#!/usr/bin/env python3
"""
01_migrate_db.py — Add clean_cafes, cafe_chains tables and extend scraped_cafes/images.

Safe to run multiple times (uses IF NOT EXISTS / column existence checks).
Does NOT touch scraper data — only adds new columns/tables.
"""
import os
import sys
import sqlite3
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))
from utils import DB_PATH

DB_PATH_ABS = os.path.abspath(os.path.join(_HERE, '..', 'data', 'seoul', 'scraped.db'))
if not os.path.exists(DB_PATH_ABS):
    DB_PATH_ABS = os.path.abspath(os.path.join(_HERE, DB_PATH))


def col_exists(conn, table, col):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def migrate(conn, db_path):
    print(f"DB: {db_path}")

    # 1. Extend scraped_cafes: belongs_to_cafe_id, name_embedding
    for col, typedef in [
        ("belongs_to_cafe_id", "TEXT"),
        ("name_embedding", "BLOB"),
    ]:
        if not col_exists(conn, "scraped_cafes", col):
            conn.execute(f"ALTER TABLE scraped_cafes ADD COLUMN {col} {typedef}")
            print(f"  scraped_cafes.{col} added")
        else:
            print(f"  scraped_cafes.{col} already exists")

    # 2. Extend images: belongs_to_cafe_id
    if not col_exists(conn, "images", "belongs_to_cafe_id"):
        conn.execute("ALTER TABLE images ADD COLUMN belongs_to_cafe_id TEXT")
        print("  images.belongs_to_cafe_id added")
    else:
        print("  images.belongs_to_cafe_id already exists")

    # 3. cafe_chains table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cafe_chains (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            name_english TEXT,
            logo        TEXT,
            name_embed  BLOB,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("  cafe_chains: ok")

    # 4. clean_cafes table — one row per physical cafe location
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clean_cafes (
            id              TEXT PRIMARY KEY,
            chain_id        TEXT REFERENCES cafe_chains(id),
            name            TEXT NOT NULL,
            english_name    TEXT,
            avg_lat         REAL NOT NULL,
            avg_lon         REAL NOT NULL,
            address         TEXT,
            url             TEXT,
            providers       TEXT,
            source_ids      TEXT,
            name_embedding  BLOB,
            tags            TEXT,
            metadata        TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Backfill for DBs created before these columns were added.
    # metadata is required — the normalizer INSERTs into it; without it a
    # clean-slate clean_cafes fails every merge ("no column named metadata").
    for col in ("tags TEXT", "metadata TEXT"):
        try:
            conn.execute(f"ALTER TABLE clean_cafes ADD COLUMN {col}")
        except Exception:
            pass
    print("  clean_cafes: ok")

    # Indexes for proximity queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clean_cafes_lat_lon ON clean_cafes(avg_lat, avg_lon)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cafes_belongs ON scraped_cafes(belongs_to_cafe_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_images_belongs ON images(belongs_to_cafe_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cafes_embedding ON scraped_cafes(name_embedding) WHERE name_embedding IS NOT NULL")

    conn.commit()
    print("Migration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_PATH_ABS, help="Path to SQLite DB")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        migrate(conn, args.db)
    finally:
        conn.close()
