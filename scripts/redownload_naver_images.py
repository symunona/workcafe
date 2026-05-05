#!/usr/bin/env python3
"""
redownload_naver_images.py — Re-download Naver images that failed (file_size = -1).

Uses same iPhone mobile UA + Referer as scraper_naver_images_v1's image download
session (plain HTTP — no Playwright needed for CDN image downloads, only for API).
Safe to re-run: skips rows where file already exists on disk or file_size > 0.

Usage (from project root):
    python scripts/redownload_naver_images.py [--dry-run] [--limit N]
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
DELAY     = 0.4

# Mirrors scraper_naver_images_v1.MOBILE_UA + IMG_HEADERS
MOBILE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 '
    'Mobile/15E148 Safari/604.1'
)

DESKTOP_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

SESSION_ROTATE_EVERY = 200


def make_session() -> requests.Session:
    s = requests.Session()
    # Try mobile UA first (same as scraper); rotate to desktop on 403/429
    s.headers.update({
        'User-Agent': MOBILE_UA,
        'Referer': 'https://pcmap.place.naver.com/',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    })
    return s


def make_desktop_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': random.choice(DESKTOP_UAS),
        'Referer': 'https://m.place.naver.com/',
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
        WHERE file_size = -1 AND provider = 'naver'
        {lim}
    """).fetchall()

    total = len(rows)
    ok = skipped = failed = rate_limited = 0
    session = make_session()
    consecutive_fail = 0

    print(f"Naver failed images to attempt: {total}{' [dry-run]' if args.dry_run else ''}")

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
                session = make_desktop_session()  # try different UA on rate limit
                time.sleep(15 * consecutive_fail)
                failed += 1
                continue
            if resp.status_code == 403:
                # Try desktop session as fallback
                session.close()
                session = make_desktop_session()
                resp = session.get(image_url, timeout=20)
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
