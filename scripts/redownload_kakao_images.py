#!/usr/bin/env python3
"""
redownload_kakao_images.py — Re-download Kakao images that failed (file_size = -1).

Uses same mobile UA + pf/appversion headers + cthumb proxy as scraper_kakao_images_v3.
Safe to re-run: skips rows where file already exists on disk or file_size > 0.

Usage (from project root):
    python scripts/redownload_kakao_images.py [--dry-run] [--limit N]

Logs to: scraper/log/redownload_kakao.log
"""

import argparse
import logging
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
LOG_FILE    = "scraper/log/redownload_kakao.log"
CTHUMB_BASE = "https://img1.kakaocdn.net/cthumb/local/C800x800.q50/?fname="

DELAY              = 0.35   # seconds between successful downloads
MAX_RETRIES        = 3      # per-image attempts before giving up
RETRY_BACKOFF      = [2, 10, 30]      # seconds between retries
RATE_LIMIT_BACKOFF = [30, 120, 300]   # seconds on 429 (escalating)
SESSION_ROTATE_EVERY = 150

DB_WRITE_RETRIES = 5
DB_WRITE_PAUSE   = 10

MOBILE_UAS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.104 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

CAPTCHA_SIGNALS = [b'captcha', b'robot', b'bot detection', b'unusual traffic',
                   b'blocked', b'challenge', b'access denied']

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ]
)
log = logging.getLogger(__name__)


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


def is_captcha(resp: requests.Response) -> bool:
    ct = resp.headers.get('Content-Type', '').lower()
    if 'text/html' in ct:
        body_lower = resp.content[:2000].lower()
        if any(sig in body_lower for sig in CAPTCHA_SIGNALS):
            return True
        # HTML response when we expected an image is already suspicious
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

    log.info(f"=== redownload_kakao_images START {'[dry-run]' if args.dry_run else ''} ===")

    conn = sqlite3.connect(CLEAN_DB)
    lim = f"LIMIT {args.limit}" if args.limit else ""
    rows = conn.execute(f"""
        SELECT id, image_url, local_path
        FROM images
        WHERE file_size = -1 AND provider = 'kakao'
        {lim}
    """).fetchall()

    total = len(rows)
    ok = skipped = failed = captchas = 0
    rate_limit_hits = 0
    session, current_ua = make_session()

    log.info(f"Kakao failed images to attempt: {total}")
    print(f"Kakao failed images: {total}{' [dry-run]' if args.dry_run else ''}")

    for i, (image_id, image_url, local_path) in enumerate(rows, 1):
        disk_path = local_to_disk(local_path)

        # File recovered by a previous partial run — just sync DB
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
            session, current_ua = make_session()
            log.info(f"Session rotated at ok={ok}, new UA: {current_ua[:60]}")

        url = resolve_url(image_url)
        data = None

        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(url, timeout=20)

                if resp.status_code == 429:
                    rate_limit_hits += 1
                    backoff = RATE_LIMIT_BACKOFF[min(rate_limit_hits - 1, len(RATE_LIMIT_BACKOFF) - 1)]
                    log.warning(f"429 rate-limit id={image_id} hit={rate_limit_hits}, sleeping {backoff}s | url={url[:80]}")
                    session.close()
                    session, current_ua = make_session()
                    time.sleep(backoff)
                    continue

                if is_captcha(resp):
                    captchas += 1
                    snippet = resp.content[:300].decode('utf-8', errors='replace').replace('\n', ' ')
                    log.warning(f"CAPTCHA id={image_id} attempt={attempt+1} status={resp.status_code} "
                                f"ct={resp.headers.get('Content-Type','?')} | body: {snippet}")
                    time.sleep(15 * (attempt + 1))
                    session.close()
                    session, current_ua = make_session()
                    continue

                if resp.status_code != 200:
                    log.warning(f"HTTP {resp.status_code} id={image_id} attempt={attempt+1} | url={url[:80]}")
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue

                if len(resp.content) < 100:
                    log.warning(f"Suspiciously small response id={image_id} attempt={attempt+1} "
                                f"size={len(resp.content)} | url={url[:80]}")
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue

                data = resp.content
                rate_limit_hits = 0
                break

            except requests.exceptions.Timeout:
                log.warning(f"Timeout id={image_id} attempt={attempt+1} | url={url[:80]}")
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
            except Exception as e:
                log.warning(f"Request error id={image_id} attempt={attempt+1}: {e} | url={url[:80]}")
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])

        if data is None:
            log.error(f"GIVE UP id={image_id} after {MAX_RETRIES} attempts | url={url[:80]}")
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
            ok += 1
        except OSError as e:
            log.error(f"save_image failed id={image_id}: {e} | disk_path={disk_path}")
            failed += 1

        if i % 200 == 0:
            conn.commit()
            log.info(f"Progress [{i}/{total}] ok={ok} skip={skipped} fail={failed} captcha={captchas} rl={rate_limit_hits}")

        time.sleep(DELAY)
        pct = 100 * i / total
        print(f"\r  [{i}/{total} {pct:.1f}%] ok={ok} skip={skipped} fail={failed} captcha={captchas} rl={rate_limit_hits}", end="", flush=True)

    conn.commit()
    session.close()
    conn.close()

    summary = f"Done. ok={ok} skipped={skipped} failed={failed} captchas={captchas} rate_limit_hits={rate_limit_hits}"
    log.info(f"=== redownload_kakao_images END: {summary} ===")
    print(f"\n{summary}")


if __name__ == "__main__":
    run()
