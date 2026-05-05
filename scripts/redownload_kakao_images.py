#!/usr/bin/env python3
"""
redownload_kakao_images.py — Re-download Kakao images that failed (file_size = -1).

Uses same mobile UA + pf/appversion headers + cthumb proxy as scraper_kakao_images_v3.
Safe to re-run: skips rows where file already exists on disk or file_size > 0.

Usage (from project root):
    python scripts/redownload_kakao_images.py [--dry-run] [--limit N]
"""

import argparse
import os
import random
import sqlite3
import sys
import time
from urllib.parse import urlparse, unquote, parse_qs, quote

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))
from image_utils import save_image

CLEAN_DB    = "data/seoul/clean.db"
DATA_DIR    = "data/seoul"
CTHUMB_BASE = "https://img1.kakaocdn.net/cthumb/local/C800x800.q50/?fname="
DELAY       = 0.35   # seconds between downloads

MOBILE_UAS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.104 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

SESSION_ROTATE_EVERY = 150   # new UA + fresh TCP pool every N downloads


def make_session() -> tuple[requests.Session, str]:
    ua = random.choice(MOBILE_UAS)
    s = requests.Session()
    s.headers.update({
        "User-Agent": ua,
        "Referer": "https://place.map.kakao.com/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "pf": "MW",
        "appversion": "6.6.0",
    })
    return s, ua


def resolve_url(raw_url: str) -> str:
    """Mirror of scraper_kakao_images_v3.resolve_url."""
    if 'cthumb' in raw_url and 'fname=' in raw_url:
        try:
            qs = parse_qs(urlparse(raw_url).query)
            if 'fname' in qs:
                raw_url = unquote(qs['fname'][0])
        except Exception:
            pass
    if 'postfiles.pstatic.net' in raw_url or 'blogfiles.pstatic.net' in raw_url:
        return CTHUMB_BASE + quote(raw_url, safe='')
    if 't1.daumcdn.net/local/kakaomapPhoto' in raw_url:
        return raw_url.split('?')[0] + '?original'
    return raw_url


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
        WHERE file_size = -1 AND provider = 'kakao'
        {lim}
    """).fetchall()

    total = len(rows)
    ok = skipped = failed = rate_limited = 0
    session, current_ua = make_session()

    print(f"Kakao failed images to attempt: {total}{' [dry-run]' if args.dry_run else ''}")

    for i, (image_id, image_url, local_path) in enumerate(rows, 1):
        disk_path = local_to_disk(local_path)

        # File already on disk from a previous partial run — just fix the DB
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

        # Rotate session periodically
        if ok > 0 and ok % SESSION_ROTATE_EVERY == 0:
            session.close()
            session, current_ua = make_session()

        url = resolve_url(image_url)
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 429:
                rate_limited += 1
                session.close()
                session, _ = make_session()
                time.sleep(30)
                failed += 1
                continue
            resp.raise_for_status()
            data = resp.content
        except Exception:
            failed += 1
            time.sleep(DELAY)
            continue

        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        try:
            actual_path, meta = save_image(data, disk_path)
            # save_image may change extension to .jpg — keep local_path consistent
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
