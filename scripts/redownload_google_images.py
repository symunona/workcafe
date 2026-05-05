#!/usr/bin/env python3
"""
redownload_google_images.py — Re-download Google images that failed (file_size = -1).

Uses same desktop UA pool + google.com Referer as scraper_google_images_v1.
Google Maps CDN URLs (lh3.googleusercontent.com) are publicly accessible.
Safe to re-run: skips rows where file already exists on disk or file_size > 0.

Usage (from project root):
    python scripts/redownload_google_images.py [--dry-run] [--limit N]

Logs to: scraper/log/redownload_google.log
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
LOG_FILE  = "scraper/log/redownload_google.log"

DELAY              = 0.5   # Google is more aggressive on rate limits
MAX_RETRIES        = 3
RETRY_BACKOFF      = [3, 15, 60]
RATE_LIMIT_BACKOFF = [60, 180, 300]  # longer — Google 429s are serious
SESSION_ROTATE_EVERY = 100

DB_WRITE_RETRIES = 5
DB_WRITE_PAUSE   = 10

# Mirrors scraper_google_images_v1.USER_AGENTS
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

CAPTCHA_SIGNALS = [b'captcha', b'robot', b'unusual traffic', b'blocked',
                   b'challenge', b'access denied', b'sorry', b'our systems']

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ]
)
log = logging.getLogger(__name__)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': random.choice(USER_AGENTS),
        'Referer': 'https://www.google.com/',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    })
    return s


def is_captcha(resp: requests.Response) -> bool:
    ct = resp.headers.get('Content-Type', '').lower()
    if 'text/html' in ct:
        body = resp.content[:2000].lower()
        # Google specifically uses "our systems have detected unusual traffic"
        if any(sig in body for sig in CAPTCHA_SIGNALS):
            return True
        return True
    return False


def local_to_disk(local_path: str) -> str:
    return os.path.join(DATA_DIR, local_path.removeprefix("/images").lstrip("/"))


def db_execute(conn: sqlite3.Connection, sql: str, params: tuple) -> bool:
    for attempt in range(DB_WRITE_RETRIES):
        try:
            conn.execute(sql, params)
            return True
        except sqlite3.OperationalError as e:
            if 'locked' in str(e) and attempt < DB_WRITE_RETRIES - 1:
                log.warning(f"DB locked, retry {attempt+1}/{DB_WRITE_RETRIES} in {DB_WRITE_PAUSE}s")
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

    log.info(f"=== redownload_google_images START {'[dry-run]' if args.dry_run else ''} ===")

    conn = sqlite3.connect(CLEAN_DB, timeout=60)
    lim = f"LIMIT {args.limit}" if args.limit else ""
    rows = conn.execute(f"""
        SELECT id, image_url, local_path
        FROM images
        WHERE file_size = -1 AND provider = 'google'
        {lim}
    """).fetchall()

    total = len(rows)
    ok = skipped = failed = captchas = 0
    rate_limit_hits = 0
    session = make_session()

    log.info(f"Google failed images to attempt: {total}")
    print(f"Google failed images: {total}{' [dry-run]' if args.dry_run else ''}")

    for i, (image_id, image_url, local_path) in enumerate(rows, 1):
        disk_path = local_to_disk(local_path)

        if os.path.exists(disk_path) and os.path.getsize(disk_path) > 0:
            if not args.dry_run:
                size = os.path.getsize(disk_path)
                db_execute(conn, "UPDATE images SET file_size = ? WHERE id = ? AND file_size = -1", (size, image_id))
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
            log.info(f"Session rotated at ok={ok}")

        data = None

        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(image_url, timeout=20)

                if resp.status_code == 429:
                    rate_limit_hits += 1
                    backoff = RATE_LIMIT_BACKOFF[min(rate_limit_hits - 1, len(RATE_LIMIT_BACKOFF) - 1)]
                    log.warning(f"429 rate-limit id={image_id} hit={rate_limit_hits}, sleeping {backoff}s | url={image_url[:80]}")
                    session.close()
                    session = make_session()
                    time.sleep(backoff)
                    continue

                if resp.status_code == 403:
                    log.warning(f"403 Forbidden id={image_id} attempt={attempt+1} | url={image_url[:80]}")
                    # Google 403 on CDN = URL expired; no point retrying same URL
                    log.error(f"GIVE UP (403 = likely expired URL) id={image_id} | url={image_url[:80]}")
                    break

                if is_captcha(resp):
                    captchas += 1
                    snippet = resp.content[:400].decode('utf-8', errors='replace').replace('\n', ' ')
                    log.warning(f"CAPTCHA id={image_id} attempt={attempt+1} status={resp.status_code} "
                                f"ct={resp.headers.get('Content-Type','?')} | body: {snippet}")
                    session.close()
                    session = make_session()
                    time.sleep(30 * (attempt + 1))
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
            db_execute(conn, """
                UPDATE images SET file_size = ?, width = ?, height = ?, local_path = ?
                WHERE id = ? AND file_size = -1
            """, (meta['file_size'], meta['width'], meta['height'], saved_local, image_id))
            conn.commit()  # commit immediately — minimise write-lock hold time vs tagger
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
    log.info(f"=== redownload_google_images END: {summary} ===")
    print(f"\n{summary}")


if __name__ == "__main__":
    run()
