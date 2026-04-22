"""
scraper_naver_metadata_v1.py — Fetch metadata for all Naver scraped_cafes.

Two-phase approach:
  Phase 1 (SQL, instant): Extract homePage/tel/roadAddress already stored in
           metadata from the original allSearch scrape. Covers ~67% of cafes.
  Phase 2 (HTTP): For cafes still missing website, call Naver Place Summary API.
           GET https://map.naver.com/p/api/place/summary?id={place_id}

Parallel: WORKERS threads.
Tracks progress with metadata_last_checked column.
"""

import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_client import DBClient
from utils import DB_PATH

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler('log/scraper_naver_metadata_v1.log'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger('naver-meta')

# ── Config ────────────────────────────────────────────────────────────────────

SUMMARY_API  = 'https://map.naver.com/p/api/place/summary?id={place_id}'
WORKERS      = 10
RATE_SLEEP   = 0.2
REFRESH_DAYS = 30
LOOP_SLEEP   = 3600 * 6

# ── Graceful shutdown ─────────────────────────────────────────────────────────

_shutdown = threading.Event()

def _sigterm(sig, frame):
    log.info("SIGTERM — finishing current batch then exiting")
    _shutdown.set()

signal.signal(signal.SIGTERM, _sigterm)

# ── Thread-local session ──────────────────────────────────────────────────────

_tl = threading.local()

def _session() -> requests.Session:
    if not hasattr(_tl, 'sess'):
        s = requests.Session()
        s.headers.update({
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/120.0.0.0 Safari/537.36'),
            'Referer': 'https://map.naver.com/',
            'Accept': 'application/json',
            'Accept-Language': 'ko-KR,ko;q=0.9',
        })
        _tl.sess = s
    return _tl.sess

# ── Phase 1: SQL backfill ─────────────────────────────────────────────────────

PHASE1_SQL = """
UPDATE scraped_cafes
SET
    metadata = json_patch(
        COALESCE(metadata, '{}'),
        json_object(
            'website', COALESCE(json_extract(metadata, '$.homePage'), ''),
            'phone',   COALESCE(json_extract(metadata, '$.tel'), '')
        )
    ),
    address = CASE
        WHEN (address IS NULL OR address = '')
             AND json_extract(metadata, '$.roadAddress') IS NOT NULL
             AND json_extract(metadata, '$.roadAddress') != ''
        THEN json_extract(metadata, '$.roadAddress')
        ELSE address
    END,
    metadata_last_checked = CURRENT_TIMESTAMP
WHERE provider = 'naver'
  AND metadata IS NOT NULL
  AND (
      json_extract(metadata, '$.homePage') IS NOT NULL
      OR json_extract(metadata, '$.tel') IS NOT NULL
      OR json_extract(metadata, '$.roadAddress') IS NOT NULL
  )
  AND (metadata_last_checked IS NULL OR metadata_last_checked < datetime('now', ?))
"""


def phase1_backfill(dbc: DBClient) -> int:
    log.info("Phase 1: extracting homePage/tel/roadAddress from existing metadata...")
    # Direct write via db_client
    result = dbc.execute(PHASE1_SQL, (f'-{REFRESH_DAYS} days',))
    count = result.get('rowcount', 0)
    log.info(f"Phase 1: updated {count} Naver cafes from existing metadata")
    return count

# ── Phase 2: Naver Place Summary API ─────────────────────────────────────────

def fetch_summary(place_id: str) -> dict | None:
    url = SUMMARY_API.format(place_id=place_id)
    try:
        r = _session().get(url, timeout=12)
        if r.status_code == 429:
            log.warning(f"429 for {place_id} — sleeping 15s")
            time.sleep(15)
            return None
        if r.status_code != 200:
            log.debug(f"  {place_id}: HTTP {r.status_code}")
            return None
        data = r.json()
        # Handle both direct and nested result structures
        return data.get('result') or data
    except Exception as e:
        log.debug(f"  {place_id}: {e}")
        return None


def parse_summary(data: dict) -> dict:
    # Naver summary API may return different field names depending on version
    website = (
        data.get('homepage') or data.get('homePage') or
        data.get('link') or data.get('siteUrl') or ''
    ).strip()
    phone = (data.get('phone') or data.get('tel') or '').strip()
    address = (
        data.get('roadAddress') or data.get('address') or
        data.get('jibunAddress') or ''
    ).strip()
    hours_raw = data.get('businessHours') or data.get('openHour') or []
    hours = hours_raw if isinstance(hours_raw, list) else []
    return {'website': website, 'phone': phone, 'address': address, 'hours': hours}


def process_phase2(dbc: DBClient, cafe_id: str, place_id: str) -> bool:
    data = fetch_summary(place_id)
    if data is None:
        dbc.execute(
            "UPDATE scraped_cafes SET metadata_last_checked = CURRENT_TIMESTAMP WHERE id = ?",
            (cafe_id,),
        )
        return False

    info = parse_summary(data)
    patch = {k: v for k, v in info.items() if k != 'address' and v}

    dbc.execute(
        """UPDATE scraped_cafes SET
               metadata = json_patch(COALESCE(metadata, '{}'), ?),
               address  = CASE WHEN ? != '' THEN ? ELSE address END,
               metadata_last_checked = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (json.dumps(patch), info['address'], info['address'], cafe_id),
    )

    time.sleep(RATE_SLEEP)
    return bool(info.get('website'))

# ── DB helpers ────────────────────────────────────────────────────────────────

def ensure_column():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute('ALTER TABLE scraped_cafes ADD COLUMN metadata_last_checked TIMESTAMP')
        conn.commit()
        log.info("Added metadata_last_checked column")
    except Exception:
        pass
    finally:
        conn.close()


def load_missing_website(conn: sqlite3.Connection) -> list[tuple]:
    """Cafes that have been phase-1 processed but still lack a website."""
    return conn.execute(
        """SELECT id, provider_id FROM scraped_cafes
           WHERE provider = 'naver'
             AND metadata_last_checked IS NOT NULL
             AND (json_extract(metadata, '$.website') IS NULL
                  OR json_extract(metadata, '$.website') = '')
           ORDER BY ROWID
           LIMIT 5000""",
    ).fetchall()


def load_unprocessed(conn: sqlite3.Connection) -> list[tuple]:
    """Cafes not yet touched by any pass."""
    return conn.execute(
        """SELECT id, provider_id FROM scraped_cafes
           WHERE provider = 'naver'
             AND (metadata_last_checked IS NULL
                  OR metadata_last_checked < datetime('now', ?))
             AND (json_extract(metadata, '$.homePage') IS NULL
                  OR json_extract(metadata, '$.homePage') = '')
           ORDER BY ROWID""",
        (f'-{REFRESH_DAYS} days',),
    ).fetchall()

# ── Main loop ─────────────────────────────────────────────────────────────────

def run_pass(dbc: DBClient):
    # Phase 1: SQL backfill from existing metadata
    phase1_backfill(dbc)

    if _shutdown.is_set():
        return

    # Phase 2: HTTP fetch for cafes still missing website
    rconn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=30)
    rconn.execute('PRAGMA journal_mode=WAL')
    rows = load_unprocessed(rconn)
    rconn.close()

    if not rows:
        log.info("Phase 2: no unprocessed cafes")
        return

    total = len(rows)
    log.info(f"Phase 2: {total} Naver cafes missing metadata — fetching from API")
    done = failed = websites = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(process_phase2, dbc, r[0], r[1]): r for r in rows}
        for fut in as_completed(futs):
            if _shutdown.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            try:
                got = fut.result()
                done += 1
                if got:
                    websites += 1
            except Exception as e:
                failed += 1
                log.warning(f"Worker error: {e}")
            if (done + failed) % 200 == 0:
                log.info(f"  {done}/{total} done, {websites} websites found, {failed} errors")

    log.info(f"Phase 2 done: {done} updated ({websites} websites), {failed} errors / {total} total")


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs('log', exist_ok=True)
    ensure_column()
    dbc = DBClient()

    while not _shutdown.is_set():
        run_pass(dbc)
        if _shutdown.is_set():
            break
        log.info(f"Pass complete — sleeping {LOOP_SLEEP // 3600}h before next refresh")
        _shutdown.wait(LOOP_SLEEP)

    dbc.close()
    log.info("Exiting.")


if __name__ == '__main__':
    main()
