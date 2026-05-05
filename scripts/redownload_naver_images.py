#!/usr/bin/env python3
"""
redownload_naver_images.py — Re-download Naver images that failed (file_size = -1).

Uses same iPhone mobile UA + Referer as scraper_naver_images_v1's image download
session (plain HTTP — no Playwright needed for CDN image downloads, only for API).
Falls back to desktop UA on 403.
Safe to re-run: skips rows where file already exists on disk or file_size > 0.

Usage (from project root):
    python scripts/redownload_naver_images.py [--dry-run] [--limit N]

Logs to: scraper/log/redownload_naver.log
"""

import argparse
import logging
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
LOG_FILE  = "scraper/log/redownload_naver.log"

DELAY              = 0.4
MAX_RETRIES        = 3
RETRY_BACKOFF      = [2, 10, 30]
RATE_LIMIT_BACKOFF = [15, 60, 180]
SESSION_ROTATE_EVERY = 200

DB_WRITE_RETRIES = 5
DB_WRITE_PAUSE   = 10

# Mirrors scraper_naver_images_v1.MOBILE_UA
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

CAPTCHA_SIGNALS = [b'captcha', b'robot', b'bot detection', b'unusual traffic',
                   b'blocked', b'challenge', b'access denied',
                   '자단'.encode(),  # Korean "차단" (blocked)
                   '보안'.encode(),  # Korean "보안" (security)
                   ]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ]
)
log = logging.getLogger(__name__)


def make_mobile_session() -> requests.Session:
    s = requests.Session()
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


def is_captcha(resp: requests.Response) -> bool:
    ct = resp.headers.get('Content-Type', '').lower()
    if 'text/html' in ct:
        body = resp.content[:2000].lower()
        if any(sig in body for sig in CAPTCHA_SIGNALS):
            return True
        return True  # any HTML response instead of an image is suspicious
    return False


def local_to_disk(local_path: str) -> str:
    return os.path.join(DATA_DIR, local_path.removeprefix("/images").lstrip("/"))


def db_execute_commit(conn: sqlite3.Connection, sql: str, params: tuple) -> bool:
    """Execute + commit atomically, retrying on lock errors."""
    for attempt in range(DB_WRITE_RETRIES):
        try:
            conn.execute(sql, params)
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < DB_WRITE_RETRIES - 1:
                log.warning(f"DB locked, retry {attempt+1}/{DB_WRITE_RETRIES} in {DB_WRITE_PAUSE}s")
                try:
                    conn.rollback()
                except Exception:
                    pass
                time.sleep(DB_WRITE_PAUSE)
            else:
                log.error(f"DB write failed after {attempt+1} attempts: {e}")
                return False
    return False


def run():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    log.info(f"=== redownload_naver_images START {'[dry-run]' if args.dry_run else ''} ===")

    conn = sqlite3.connect(CLEAN_DB, timeout=60)
    lim = f"LIMIT {args.limit}" if args.limit else ""
    rows = conn.execute(f"""
        SELECT id, image_url, local_path
        FROM images
        WHERE file_size = -1 AND provider = 'naver'
        {lim}
    """).fetchall()

    total = len(rows)
    ok = skipped = failed = captchas = 0
    rate_limit_hits = 0
    # Start with mobile session (matches original scraper)
    session = make_mobile_session()
    using_desktop = False

    log.info(f"Naver failed images to attempt: {total}")
    print(f"Naver failed images: {total}{' [dry-run]' if args.dry_run else ''}")

    for i, (image_id, image_url, local_path) in enumerate(rows, 1):
        disk_path = local_to_disk(local_path)

        if os.path.exists(disk_path) and os.path.getsize(disk_path) > 0:
            if not args.dry_run:
                size = os.path.getsize(disk_path)
                db_execute_commit(conn, "UPDATE images SET file_size = ? WHERE id = ? AND file_size = -1", (size, image_id))
            skipped += 1
            continue

        if args.dry_run:
            ok += 1
            continue

        if ok > 0 and ok % SESSION_ROTATE_EVERY == 0:
            session.close()
            session = make_mobile_session()
            using_desktop = False
            log.info(f"Session rotated at ok={ok} → mobile UA")

        data = None

        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(image_url, timeout=20)

                if resp.status_code == 429:
                    rate_limit_hits += 1
                    backoff = RATE_LIMIT_BACKOFF[min(rate_limit_hits - 1, len(RATE_LIMIT_BACKOFF) - 1)]
                    log.warning(f"429 rate-limit id={image_id} hit={rate_limit_hits}, sleeping {backoff}s | url={image_url[:80]}")
                    session.close()
                    session = make_desktop_session()
                    using_desktop = True
                    time.sleep(backoff)
                    continue

                if resp.status_code == 403:
                    log.warning(f"403 Forbidden id={image_id} attempt={attempt+1} desktop={using_desktop} | url={image_url[:80]}")
                    if not using_desktop:
                        session.close()
                        session = make_desktop_session()
                        using_desktop = True
                        log.info(f"Switched to desktop UA for id={image_id}")
                        continue
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue

                if is_captcha(resp):
                    captchas += 1
                    snippet = resp.content[:300].decode('utf-8', errors='replace').replace('\n', ' ')
                    log.warning(f"CAPTCHA id={image_id} attempt={attempt+1} status={resp.status_code} "
                                f"ct={resp.headers.get('Content-Type','?')} | body: {snippet}")
                    session.close()
                    session = make_desktop_session() if not using_desktop else make_mobile_session()
                    using_desktop = not using_desktop
                    time.sleep(20 * (attempt + 1))
                    continue

                if resp.status_code != 200:
                    log.warning(f"HTTP {resp.status_code} id={image_id} attempt={attempt+1} | url={image_url[:80]}")
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue

                if len(resp.content) < 100:
                    log.warning(f"Tiny response id={image_id} attempt={attempt+1} size={len(resp.content)} | url={image_url[:80]}")
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue

                data = resp.content
                rate_limit_hits = 0
                break

            except requests.exceptions.Timeout:
                log.warning(f"Timeout id={image_id} attempt={attempt+1} | url={image_url[:80]}")
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
            except Exception as e:
                log.warning(f"Request error id={image_id} attempt={attempt+1}: {e} | url={image_url[:80]}")
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])

        if data is None:
            log.error(f"GIVE UP id={image_id} after {MAX_RETRIES} attempts | url={image_url[:80]}")
            failed += 1
            time.sleep(DELAY)
            pct = 100 * i / total
            print(f"\r  [{i}/{total} {pct:.1f}%] ok={ok} skip={skipped} fail={failed} captcha={captchas} rl={rate_limit_hits}", end="", flush=True)
            continue

        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        try:
            actual_path, meta = save_image(data, disk_path)
            saved_local = "/images" + actual_path.removeprefix(DATA_DIR)
            db_execute_commit(conn, """
                UPDATE images SET file_size = ?, width = ?, height = ?, local_path = ?
                WHERE id = ? AND file_size = -1
            """, (meta['file_size'], meta['width'], meta['height'], saved_local, image_id))
            ok += 1
        except OSError as e:
            log.error(f"save_image failed id={image_id}: {e} | disk_path={disk_path}")
            failed += 1

        if i % 200 == 0:
            log.info(f"Progress [{i}/{total}] ok={ok} skip={skipped} fail={failed} captcha={captchas} rl={rate_limit_hits}")

        time.sleep(DELAY)
        pct = 100 * i / total
        print(f"\r  [{i}/{total} {pct:.1f}%] ok={ok} skip={skipped} fail={failed} captcha={captchas} rl={rate_limit_hits}", end="", flush=True)

    conn.commit()
    session.close()
    conn.close()

    summary = f"Done. ok={ok} skipped={skipped} failed={failed} captchas={captchas} rate_limit_hits={rate_limit_hits}"
    log.info(f"=== redownload_naver_images END: {summary} ===")
    print(f"\n{summary}")


if __name__ == "__main__":
    run()
