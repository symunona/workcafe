#!/usr/bin/env python3
"""
00_dedup_raw_cafes.py — Remove duplicate raw cafe entries.

Duplicates = same (provider, lat, lon). Happens when scraper runs multiple
times and the same place gets a different ID string (e.g. Google place URLs
vs short hex IDs). Keeps the row with the latest scraped_at; on tie, prefers
the longer ID (URL-format IDs from newer scraper are longer).

Safe to re-run: only deletes rows where a newer/longer duplicate exists.
Run BEFORE the normalize pipeline.
"""
import os
import sys
import sqlite3
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))
from db_client import DBClient

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.abspath(os.path.join(_HERE, '..', '..', 'data', 'seoul', 'cafedata.db')), help="Path to DB")
    parser.add_argument("--socket", default="/tmp/workcafe_db.sock", help="Unix socket path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    dbc = DBClient(socket_path=args.socket)

    # Find all duplicate groups
    rows = conn.execute("""
        SELECT provider, lat, lon, COUNT(*) as n
        FROM cafes
        GROUP BY provider, lat, lon
        HAVING n > 1
    """).fetchall()

    print(f"Duplicate groups: {len(rows)}")
    total_dupes = sum(r[3] - 1 for r in rows)
    print(f"Rows to delete:   {total_dupes}")

    to_delete = []
    for provider, lat, lon, _ in rows:
        entries = conn.execute(
            "SELECT id, scraped_at FROM cafes WHERE provider=? AND lat=? AND lon=? ORDER BY scraped_at DESC, length(id) DESC",
            (provider, lat, lon)
        ).fetchall()
        # Keep first (latest scraped_at, then longest id), delete the rest
        for loser_id, _ in entries[1:]:
            to_delete.append(loser_id)

    print(f"Deleting {len(to_delete)} duplicate cafe rows...")
    for cafe_id in to_delete:
        dbc.execute("DELETE FROM images WHERE cafe_id = ?", (cafe_id,))
        dbc.execute("DELETE FROM cafes WHERE id = ?", (cafe_id,))

    remaining = conn.execute(
        "SELECT COUNT(*) FROM (SELECT provider, lat, lon FROM cafes GROUP BY provider, lat, lon HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    print(f"Remaining duplicate groups: {remaining}")
    print("Done. Now run: echo y | python3 scraper/normalize/db_clean.py && cd scraper && /usr/bin/python3 normalize/04_normalize_pipeline.py")


if __name__ == "__main__":
    main()
