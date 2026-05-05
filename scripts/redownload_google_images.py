#!/usr/bin/env python3
"""
redownload_google_images.py — Re-download Google images that failed (file_size = -1).

Uses same desktop UA pool + google.com Referer as scraper_google_images_v1.
No proxy/Playwright needed — Google Maps CDN URLs (lh3.googleusercontent.com) are
publicly accessible. Safe to re-run: skips rows where file already exists or
file_size > 0.

Usage (from project root):
    python scripts/redownload_google_images.py [--dry-run] [--limit N]
"""

import argparse
import os
import random
import sqlite3
import sys
import time

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))
from image_utils import save_image

CLEAN_DB = "data/seoul/clean.db"
DATA_DIR  = "data/seoul"
DELAY     = 0.5   # slightly slower — Google is more aggressive on rate limits

# Mirrors scraper_google_images_v1.USER_AGENTS
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

SESSION_ROTATE_EVERY = 100


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': random.choice(USER_AGENTS),
        'Referer': 'https://www.google.com/',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    })
    return s


def local_to_disk(local_path: str) -> str:
    return os.path.join(DATA_DIR, local_path.removeprefix("/images").lstrip("/"))


def run():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    conn = sqlite3.connect(CLEAN_DB)

    lim = f"LIMIT {args.limit}" if args.limit else ""
    rows = conn.execute(f"""
        SELECT id, image_url, local_path
        FROM images
        WHERE file_size = -1 AND provider = 'google'
        {lim}
    """).fetchall()

    total = len(rows)
    ok = skipped = failed = rate_limited = 0
    session = make_session()
    consecutive_fail = 0

    print(f"Google failed images to attempt: {total}{' [dry-run]' if args.dry_run else ''}")

    for i, (image_id, image_url, local_path) in enumerate(rows, 1):
        disk_path = local_to_disk(local_path)

        if os.path.exists(disk_path) and os.path.getsize(disk_path) > 0:
            if not args.dry_run:
                size = os.path.getsize(disk_path)
                conn.execute(
                    "UPDATE images SET file_size = ? WHERE id = ? AND file_size = -1",
                    (size, image_id),
                )
                if i % 500 == 0:
                    conn.commit()
            skipped += 1
            continue

        if args.dry_run:
            ok += 1
            continue

        if ok > 0 and ok % SESSION_ROTATE_EVERY == 0:
            session.close()
            session = make_session()

        try:
            resp = session.get(image_url, timeout=20)
            if resp.status_code == 429:
                rate_limited += 1
                consecutive_fail += 1
                session.close()
                session = make_session()
                backoff = min(60 * consecutive_fail, 300)
                time.sleep(backoff)
                failed += 1
                continue
            resp.raise_for_status()
            data = resp.content
            consecutive_fail = 0
        except Exception:
            consecutive_fail += 1
            failed += 1
            time.sleep(DELAY)
            continue

        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        try:
            actual_path, meta = save_image(data, disk_path)
            saved_local = "/images" + actual_path.removeprefix(DATA_DIR)
            conn.execute("""
                UPDATE images
                SET file_size = ?, width = ?, height = ?, local_path = ?
                WHERE id = ? AND file_size = -1
            """, (meta['file_size'], meta['width'], meta['height'], saved_local, image_id))
            ok += 1
        except OSError:
            failed += 1

        if i % 200 == 0:
            conn.commit()

        time.sleep(DELAY)
        pct = 100 * i / total
        print(f"\r  [{i}/{total} {pct:.1f}%] ok={ok} skip={skipped} fail={failed} rl={rate_limited}", end="", flush=True)

    conn.commit()
    session.close()
    conn.close()
    print(f"\nDone. ok={ok} skipped={skipped} failed={failed} rate_limited={rate_limited}")


if __name__ == "__main__":
    run()
