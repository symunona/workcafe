"""
scraper_kakao_metadata_v1.py — Fetch metadata for all Kakao scraped_cafes.

API: GET https://place-api.map.kakao.com/places/panel3/{place_id}
Requires header: pf: MW
Returns summary.homepages[], summary.phone_numbers[].tel, summary.address.road, open_hours.

Parallel: WORKERS threads, each owns a requests.Session.
Tracks progress with metadata_last_checked column.
Loops: after finishing all pending, waits REFRESH_DAYS before re-running.
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
        logging.FileHandler('log/scraper_kakao_metadata_v1.log'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger('kakao-meta')

# ── Config ────────────────────────────────────────────────────────────────────

DETAIL_API   = 'https://place-api.map.kakao.com/places/panel3/{place_id}'
WORKERS      = 15
RATE_SLEEP   = 0.15    # per-worker pause between requests (s)
REFRESH_DAYS = 30      # re-check cafes older than this
LOOP_SLEEP   = 3600 * 6  # sleep between complete passes (s)

# ── Graceful shutdown ─────────────────────────────────────────────────────────

_shutdown = threading.Event()

def _sigterm(sig, frame):
    log.info("SIGTERM — finishing current batch then exiting")
    _shutdown.set()

signal.signal(signal.SIGTERM, _sigterm)

# ── Thread-local session ──────────────────────────────────────────────────────

_tl = threading.local()

UAS = [
    'Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 Chrome/112.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 Chrome/110.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Version/16.0 Mobile Safari/604.1',
]

def _session() -> requests.Session:
    if not hasattr(_tl, 'sess'):
        import random
        s = requests.Session()
        s.headers.update({
            'User-Agent': random.choice(UAS),
            'Referer': 'https://map.kakao.com/',
            'Accept': 'application/json',
            'Accept-Language': 'ko-KR,ko;q=0.9',
            'pf': 'MW',
        })
        _tl.sess = s
    return _tl.sess

# ── Kakao API ─────────────────────────────────────────────────────────────────

def fetch_detail(place_id: str) -> dict | None:
    url = DETAIL_API.format(place_id=place_id)
    try:
        r = _session().get(url, timeout=12)
        if r.status_code == 429:
            log.warning(f"429 for {place_id} — sleeping 10s")
            time.sleep(10)
            return None
        if r.status_code != 200:
            log.debug(f"  {place_id}: HTTP {r.status_code}")
            return None
        return r.json()
    except Exception as e:
        log.debug(f"  {place_id}: {e}")
        return None


def parse_info(data: dict) -> dict:
    summary = data.get('summary') or {}
    addr_obj = summary.get('address') or {}
    address = addr_obj.get('road') or addr_obj.get('disp') or ''

    homepages = summary.get('homepages') or []
    website = homepages[0].strip() if homepages else ''

    phone_numbers = summary.get('phone_numbers') or []
    phone = (phone_numbers[0].get('tel') or '').strip() if phone_numbers else ''

    # open_hours.week_from_today has per-day schedule
    oh = data.get('open_hours') or {}
    periods = (oh.get('week_from_today') or {}).get('week_periods') or []
    hours = []
    for p in periods:
        days = p.get('days') or []
        for day in days:
            desc = day.get('day_of_the_week_desc') or ''
            on = day.get('on_days') or {}
            time_desc = on.get('start_end_time_desc') or ''
            if time_desc:
                hours.append({'day': desc, 'time': time_desc})

    return {
        'website': website,
        'phone':   phone,
        'address': address.strip(),
        'hours':   hours,
    }

# ── Worker ────────────────────────────────────────────────────────────────────

def process(dbc: DBClient, cafe_id: str, place_id: str) -> bool:
    data = fetch_detail(place_id)

    # Always mark as checked — even if API returned no data
    if data is None:
        dbc.execute(
            "UPDATE scraped_cafes SET metadata_last_checked = CURRENT_TIMESTAMP WHERE id = ?",
            (cafe_id,),
        )
        return False

    info = parse_info(data)
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


def load_pending(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """SELECT id, provider_id FROM scraped_cafes
           WHERE provider = 'kakao'
             AND (metadata_last_checked IS NULL
                  OR metadata_last_checked < datetime('now', ?))
           ORDER BY ROWID""",
        (f'-{REFRESH_DAYS} days',),
    ).fetchall()

# ── Main loop ─────────────────────────────────────────────────────────────────

def run_pass(dbc: DBClient) -> int:
    rconn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=30)
    rconn.execute('PRAGMA journal_mode=WAL')
    rows = load_pending(rconn)
    rconn.close()

    total = len(rows)
    if not total:
        log.info("No pending Kakao cafes — pass complete")
        return 0

    log.info(f"Pass: {total} Kakao cafes to fetch metadata for")
    done = failed = websites = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(process, dbc, r[0], r[1]): r for r in rows}
        for fut in as_completed(futs):
            if _shutdown.is_set():
                log.info("Shutdown — cancelling remaining futures")
                pool.shutdown(wait=False, cancel_futures=True)
                break
            try:
                got_website = fut.result()
                done += 1
                if got_website:
                    websites += 1
            except Exception as e:
                failed += 1
                log.warning(f"Worker error: {e}")
            if (done + failed) % 500 == 0:
                log.info(f"  {done}/{total} done, {websites} websites found, {failed} errors")

    log.info(f"Pass done: {done} updated ({websites} websites), {failed} errors / {total} total")
    return total


def main():
    ensure_column()
    dbc = DBClient()

    while not _shutdown.is_set():
        processed = run_pass(dbc)
        if _shutdown.is_set():
            break
        if processed == 0:
            log.info(f"All up to date — sleeping {LOOP_SLEEP // 3600}h")
            _shutdown.wait(LOOP_SLEEP)
        else:
            # Brief pause between passes
            _shutdown.wait(60)

    dbc.close()
    log.info("Exiting.")


if __name__ == '__main__':
    main()
