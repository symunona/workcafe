#!/usr/bin/env python3
"""
Roll up image_tags → clean_cafes.tags

Writes a JSON object {tag: image_count} to clean_cafes.tags for every cafe
that has at least one tagged image.  Run after tag_images_clip.py.

Run from project root:
    python scripts/tag_cafes_rollup.py
"""

import argparse, sqlite3, json

p = argparse.ArgumentParser()
p.add_argument("--db", default="data/seoul/clean.db")
DB = p.parse_args().db

conn = sqlite3.connect(DB)

try:
    conn.execute("ALTER TABLE clean_cafes ADD COLUMN tags TEXT")
    conn.commit()
    print("Added tags column to clean_cafes")
except Exception:
    print("tags column already exists")

# Aggregate image tag counts per clean_cafe
rows = conn.execute("""
    SELECT c.belongs_to_cafe_id, it.tag, COUNT(*) as cnt
    FROM image_tags it
    JOIN images i     ON i.id  = it.image_id
    JOIN scraped_cafes c ON c.id = i.cafe_id
    WHERE c.belongs_to_cafe_id IS NOT NULL
    GROUP BY c.belongs_to_cafe_id, it.tag
""").fetchall()

cafe_tags: dict[str, dict[str, int]] = {}
for cafe_id, tag, cnt in rows:
    cafe_tags.setdefault(cafe_id, {})[tag] = cnt

conn.executemany(
    "UPDATE clean_cafes SET tags = ? WHERE id = ?",
    [(json.dumps(dict(sorted(tags.items(), key=lambda x: -x[1]))), cid)
     for cid, tags in cafe_tags.items()]
)
conn.commit()

total_tags = conn.execute("SELECT COUNT(*) FROM clean_cafes WHERE tags IS NOT NULL").fetchone()[0]
print(f"Updated {len(cafe_tags)} cafes with tags ({total_tags} total in DB)")

# Show distribution
dist = conn.execute("""
    SELECT je.key, COUNT(*) as cafes
    FROM clean_cafes, json_each(tags) je
    WHERE tags IS NOT NULL
    GROUP BY je.key ORDER BY cafes DESC
""").fetchall()
print("\nTag distribution (cafes per tag):")
for tag, n in dist:
    print(f"  {tag:<30} {n}")

conn.close()
