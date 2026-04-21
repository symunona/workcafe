#!/usr/bin/env python3
"""
Backfill local_images for google scraped_cafes that have images on disk but no path in metadata.
Also clears bad local_images paths that point to non-existent files.
"""
import os, json, sqlite3, re

DB = '../data/seoul/cafedata.db'
DATA_DIR = '../data/seoul'
GOOGLE_DIR = os.path.join(DATA_DIR, 'google')

def normalize(pid):
    return re.sub(r'[^a-zA-Z0-9]+', '_', pid).strip('_')[:120]

os.chdir(os.path.dirname(os.path.abspath(__file__)))
conn = sqlite3.connect(DB)

backfilled = 0
cleared = 0
skipped = 0

for row in conn.execute("SELECT id, provider_id, metadata FROM scraped_cafes WHERE provider='google'"):
    cafe_id, provider_id, meta_str = row
    meta = json.loads(meta_str or '{}')
    existing = meta.get('local_images', [])

    # Check if existing paths are valid on disk
    if existing:
        valid = []
        for p in existing:
            disk = os.path.join(DATA_DIR, p.replace('/images/', '', 1))
            if os.path.exists(disk):
                valid.append(p)
        if len(valid) == len(existing):
            skipped += 1
            continue  # all good
        if valid:
            # Some valid, some not — keep valid ones
            meta['local_images'] = valid
            conn.execute("UPDATE scraped_cafes SET metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), cafe_id))
            cleared += 1
            continue
        # None valid — fall through to check disk by safe_id

    # Check disk for images using safe_id derived from provider_id
    safe_id = normalize(provider_id)
    img_dir = os.path.join(GOOGLE_DIR, safe_id, 'images')
    if os.path.isdir(img_dir):
        files = sorted(f for f in os.listdir(img_dir) if f.startswith('img_'))
        if files:
            meta['local_images'] = [f"/images/google/{safe_id}/images/{f}" for f in files]
            conn.execute("UPDATE scraped_cafes SET metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), cafe_id))
            backfilled += 1
            if backfilled % 100 == 0:
                conn.commit()
                print(f"Backfilled {backfilled}...")
            continue

    # No images anywhere — clear bad paths if any
    if existing:
        meta['local_images'] = []
        conn.execute("UPDATE scraped_cafes SET metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), cafe_id))
        cleared += 1

conn.commit()
conn.close()
print(f"Done: {backfilled} backfilled, {cleared} bad paths cleared, {skipped} already OK")
