"""
scraper_kakao_images_v3.py
==========================
WHAT WORKS (v2 → v3):
  - All v2 fixes retained: pf:MW + appversion:6.6.0 headers, cthumb proxy for blog CDN
    (C800x800.q50 only — higher sizes return 403, keep ?type= in encoded URL)
  - Paginated REST API: place-api.map.kakao.com/places/tab/photos/{id}?page=N

NEW IN v3:
  - Per-image metadata stored in the `images` DB table:
      cafe_id, provider, local_path, image_url, gallery_url, photo_id, photo_type,
      tags (JSON array), registered_at, width, height, file_size,
      exif_date, exif_lat, exif_lon
  - Image dimensions + file size via Pillow after download
  - EXIF GPS and DateTimeOriginal extracted where present
  - gallery_url: place.map.kakao.com/{place_id}#photo/{photo_id}
  - MAX_PAGES raised to 500 (was 50 in v1, fixed in v2)

WHAT DID NOT WORK / LIMITATIONS:
  - Blog CDN (postfiles.pstatic.net) returns EXIF-stripped images — no GPS/date from those
  - Kakao's per-photo API doesn't return indoor/outdoor/food/menu per individual photo,
    only as aggregate counts. Those counts are stored in photo_counts in cafe metadata.
  - VOD type photos are video thumbnails — downloaded as JPEGs, tagged type=VOD

Usage:
    cd scraper && source ../venv/bin/activate
    python scraper_kakao_images_v3.py [--limit N] [--cafe-id kakao_XXXXX] [--force]
"""

import os
import sys
import json
import time
import random
import logging
import argparse
import struct
import signal
import threading
from urllib.parse import urlparse, unquote, parse_qs, quote
from io import BytesIO

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

import requests
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from utils import DATA_DIR, normalize_provider_id, get_tor_session
from db_client import DBClient
from disk_check import check_disk_limit, DiskLimitExceeded
from image_utils import save_image

_shutdown = threading.Event()


def _sigterm(sig, frame):
    log.info("SIGTERM received — finishing current page then exiting.")
    _shutdown.set()


signal.signal(signal.SIGTERM, _sigterm)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_HERE, '..', 'log', 'scraper_kakao_images_v3.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

PHOTOS_API       = "https://place-api.map.kakao.com/places/tab/photos/{place_id}?page={page}"
CTHUMB_BASE      = "https://img1.kakaocdn.net/cthumb/local/C800x800.q50/?fname="
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

# UA pool — rotated on every session refresh (every SESSION_REFRESH_EVERY scraped_cafes).
# pf/appversion headers are fixed Kakao app fingerprints and cannot be changed.
MOBILE_UAS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.104 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
]

def _kakao_headers(ua: str | None = None) -> dict:
    return {
        "User-Agent": ua or random.choice(MOBILE_UAS),
        "Referer": "https://place.map.kakao.com/",
        "Accept": "application/json, text/plain, */*",
        "pf": "MW",
        "appversion": "6.6.0",
    }

def _img_headers(ua: str | None = None) -> dict:
    return {
        "User-Agent": ua or random.choice(MOBILE_UAS),
        "Referer": "https://place.map.kakao.com/",
    }

DELAY_BETWEEN_PAGES   = 0.8
DELAY_BETWEEN_CAFES   = (1.2, 2.5)
DELAY_BETWEEN_IMGS    = 0.2
PAGE_TIMEOUT_SECS     = 3 * 60    # watchdog: abort a single page fetch after 3 min
SESSION_REFRESH_EVERY = 100       # rebuild session (new UA + fresh TCP pool) every N pages
MAX_ATTEMPT_COUNT     = 3         # flag cafe after this many page-1 zero-result attempts

# 429 escalation: sleep durations in seconds before each retry.
# After the last entry, raise PersistentRateLimit → service restart.
RATE_LIMIT_BACKOFF = [30, 300, 1800]   # 30s → 5 min → 30 min → restart


class CafeTimeout(Exception):
    pass


class PersistentRateLimit(Exception):
    pass


def _alarm_handler(signum, frame):
    raise CafeTimeout(f"Per-cafe watchdog fired after {PAGE_TIMEOUT_SECS // 60} min")


signal.signal(signal.SIGALRM, _alarm_handler)


def cycle_tor_circuit() -> bool:
    """Send NEWNYM to get a fresh Tor exit node. Returns True on success."""
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()   # uses CookieAuthentication if enabled
            ctrl.signal(Signal.NEWNYM)
            time.sleep(5)         # Tor needs ~5s to build a new circuit
            log.info("  Tor circuit cycled (NEWNYM sent)")
            return True
    except Exception as e:
        log.debug(f"  Tor NEWNYM failed (control port not available?): {e}")
        return False


def make_session(use_tor: bool = False) -> tuple[requests.Session, str]:
    """Create a fresh session with a random UA. Returns (session, ua)."""
    ua = random.choice(MOBILE_UAS)
    session = get_tor_session() if use_tor else requests.Session()
    session.headers.update(_img_headers(ua))
    return session, ua


# ── URL resolution ──────────────────────────────────────────────────────────

def resolve_url(raw_url: str) -> str:
    if 'cthumb' in raw_url and 'fname=' in raw_url:
        try:
            qs = parse_qs(urlparse(raw_url).query)
            if 'fname' in qs:
                raw_url = unquote(qs['fname'][0])
        except Exception:
            pass
    if 'postfiles.pstatic.net' in raw_url or 'blogfiles.pstatic.net' in raw_url:
        # Keep ?type= param — stripping it causes cthumb 403
        return CTHUMB_BASE + quote(raw_url, safe='')
    if 't1.daumcdn.net/local/kakaomapPhoto' in raw_url:
        base = raw_url.split('?')[0]
        return base + '?original'
    return raw_url


# ── EXIF extraction ─────────────────────────────────────────────────────────

def _convert_gps_rational(value):
    """Convert EXIF GPS rational tuple (degrees, minutes, seconds) → decimal degrees."""
    try:
        d = float(value[0])
        m = float(value[1]) / 60.0
        s = float(value[2]) / 3600.0
        return d + m + s
    except Exception:
        return None


def extract_image_meta(data: bytes) -> dict:
    """
    Open image bytes with Pillow and extract:
      - width, height, file_size
      - exif_date (ISO format)
      - exif_lat, exif_lon (decimal degrees, None if absent)
    Returns a dict with those keys (values may be None).
    """
    result = {
        'width': None, 'height': None, 'file_size': len(data),
        'exif_date': None, 'exif_lat': None, 'exif_lon': None,
    }
    try:
        img = Image.open(BytesIO(data))
        result['width'], result['height'] = img.size

        exif_data = img._getexif()
        if not exif_data:
            return result

        # Build tag-name → value map
        named = {TAGS.get(k, k): v for k, v in exif_data.items()}

        # Date
        for date_tag in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
            if date_tag in named:
                raw = named[date_tag]
                # EXIF date: "YYYY:MM:DD HH:MM:SS" → ISO
                try:
                    result['exif_date'] = raw.replace(':', '-', 2)
                except Exception:
                    pass
                break

        # GPS
        gps_info = named.get('GPSInfo')
        if gps_info:
            gps = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}
            lat_vals = gps.get('GPSLatitude')
            lat_ref  = gps.get('GPSLatitudeRef', 'N')
            lon_vals = gps.get('GPSLongitude')
            lon_ref  = gps.get('GPSLongitudeRef', 'E')
            if lat_vals and lon_vals:
                lat = _convert_gps_rational(lat_vals)
                lon = _convert_gps_rational(lon_vals)
                if lat is not None and lon is not None:
                    result['exif_lat'] = lat if lat_ref == 'N' else -lat
                    result['exif_lon'] = lon if lon_ref == 'E' else -lon
    except Exception:
        pass
    return result


# ── API fetching ─────────────────────────────────────────────────────────────

def fetch_one_page(session, place_id: str, page: int, ua: str | None = None):
    """
    Fetch a single page from the Kakao photos API.
    Returns (photos_list, total, counts, has_next).
    total and counts are only populated on page 1 (0/{} otherwise).
    Raises PersistentRateLimit if 429 persists through all backoff stages.
    """
    api_url = PHOTOS_API.format(place_id=place_id, page=page)
    rate_limit_hits = 0

    while True:
        try:
            resp = session.get(api_url, headers=_kakao_headers(ua), timeout=10)
            if resp.status_code == 404:
                return [], 0, {}, False
            if resp.status_code == 429:
                rate_limit_hits += 1
                if rate_limit_hits > len(RATE_LIMIT_BACKOFF):
                    raise PersistentRateLimit(
                        f"429 persisted after {rate_limit_hits} attempts — forcing restart"
                    )
                sleep_s = RATE_LIMIT_BACKOFF[rate_limit_hits - 1]
                log.warning(f"  Rate limited (hit #{rate_limit_hits}), sleeping {sleep_s}s")
                if rate_limit_hits >= 2:
                    cycle_tor_circuit()
                time.sleep(sleep_s)
                continue
            rate_limit_hits = 0
            resp.raise_for_status()
            data = resp.json()
        except PersistentRateLimit:
            raise
        except Exception as e:
            log.error(f"  API error page {page}: {e}")
            return [], 0, {}, False

        counts = data.get('counts', {}) if page == 1 else {}
        total  = counts.get('total', 0) if page == 1 else 0
        photos  = data.get('photos', [])
        has_next = bool(data.get('has_next', False))
        return photos, total, counts, has_next


# ── Image download ───────────────────────────────────────────────────────────

def download_bytes(session, url) -> bytes | None:
    try:
        resp = session.get(url, stream=False, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        log.debug(f"    Download failed {url[:80]}: {e}")
        return None


# ── DB helpers ───────────────────────────────────────────────────────────────

def image_row_exists(dbc, cafe_id: str, photo_id: str) -> bool:
    if not photo_id:
        return False
    return dbc.fetchone('SELECT 1 FROM images WHERE cafe_id=? AND photo_id=?', (cafe_id, photo_id)) is not None


def insert_image_row(dbc, row: dict):
    belongs_to = dbc.fetchval(
        'SELECT belongs_to_cafe_id FROM scraped_cafes WHERE id = ?', (row['cafe_id'],)
    )
    dbc.execute('''
        INSERT OR REPLACE INTO images
          (cafe_id, provider, local_path, image_url, gallery_url,
           photo_id, photo_type, tags, registered_at,
           width, height, file_size, exif_date, exif_lat, exif_lon,
           belongs_to_cafe_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        row['cafe_id'], row['provider'], row.get('local_path'), row.get('image_url'),
        row.get('gallery_url'), row.get('photo_id'), row.get('photo_type'),
        json.dumps(row.get('tags', []), ensure_ascii=False),
        row.get('registered_at'),
        row.get('width'), row.get('height'), row.get('file_size'),
        row.get('exif_date'), row.get('exif_lat'), row.get('exif_lon'),
        belongs_to,
    ))


# ── Scrape state table ───────────────────────────────────────────────────────

def init_scrape_state(dbc):
    """
    Create kakao_scrape_state if absent, insert rows for every Kakao cafe.
    Returns count of pending scraped_cafes.
    """
    dbc.execute('''
        CREATE TABLE IF NOT EXISTS kakao_scrape_state (
            cafe_id       TEXT PRIMARY KEY,
            next_page     INTEGER DEFAULT 1,
            attempt_count INTEGER DEFAULT 0,
            status        TEXT    DEFAULT 'pending',
            last_attempted TIMESTAMP,
            FOREIGN KEY (cafe_id) REFERENCES scraped_cafes(id)
        )
    ''')
    dbc.execute('''
        INSERT OR IGNORE INTO kakao_scrape_state (cafe_id)
        SELECT id FROM scraped_cafes WHERE provider = 'kakao'
    ''')

    pending   = dbc.fetchval("SELECT COUNT(*) FROM kakao_scrape_state WHERE status='pending'")
    exhausted = dbc.fetchval("SELECT COUNT(*) FROM kakao_scrape_state WHERE status='exhausted'")
    flagged   = dbc.fetchval("SELECT COUNT(*) FROM kakao_scrape_state WHERE status='flagged'")
    log.info(f"Scrape state — pending:{pending}  exhausted:{exhausted}  flagged:{flagged}")
    return pending


def pick_random_pending_cafe(dbc):
    """
    Return (cafe_id, next_page, attempt_count, provider_id, metadata) for a
    randomly chosen cafe from the cohort with the lowest next_page among all
    pending scraped_cafes. Returns None when nothing is pending.
    """
    row = dbc.fetchone('''
        SELECT s.cafe_id, s.next_page, s.attempt_count, c.provider_id, c.metadata
        FROM   kakao_scrape_state s
        JOIN   scraped_cafes c ON c.id = s.cafe_id
        WHERE  s.status = 'pending'
          AND  s.next_page = (SELECT MIN(s2.next_page)
                              FROM   kakao_scrape_state s2
                              JOIN   scraped_cafes c2 ON c2.id = s2.cafe_id
                              WHERE  s2.status = 'pending')
        ORDER BY RANDOM()
        LIMIT 1
    ''')
    return row


# ── Per-page processing ───────────────────────────────────────────────────────

def process_one_page(dbc, session, cafe_id, provider_id, metadata, page, ua=None):
    """
    Fetch one API page for cafe_id, download any new images, write DB rows.
    Returns (new_downloads, has_next, total_from_api).
    total_from_api is 0 for pages > 1 (API only returns it on page 1).
    """
    safe_id = normalize_provider_id(provider_id)
    img_dir = os.path.join(DATA_DIR, 'kakao', safe_id, 'images')
    os.makedirs(img_dir, exist_ok=True)

    photos, total, counts, has_next = fetch_one_page(session, provider_id, page, ua=ua)

    if page == 1:
        log.info(f"  total={total} counts={counts}")

    # Filename index starts after however many images are already in DB
    db_count = dbc.fetchval('SELECT COUNT(*) FROM images WHERE cafe_id=?', (cafe_id,)) or 0
    idx = db_count

    all_local = list(metadata.get('local_images', []))
    new_downloads = 0
    failed = 0
    seen_ids = set()

    for ph in photos:
        raw = ph.get('url', '')
        if not raw:
            continue
        photo_id = str(ph.get('photo_id', ''))
        if photo_id in seen_ids:
            continue
        seen_ids.add(photo_id)

        # Already in DB — skip, don't count toward idx so filenames stay dense
        if image_row_exists(dbc, cafe_id, photo_id):
            continue

        resolved = resolve_url(raw)
        fname     = f"photo_{idx:04d}.jpg"
        save_path = os.path.join(img_dir, fname)
        local_path = f"/images/kakao/{safe_id}/images/{fname}"

        # File on disk but DB row missing — backfill
        if os.path.exists(save_path):
            img_meta = extract_image_meta(open(save_path, 'rb').read())
            insert_image_row(dbc, {
                'cafe_id': cafe_id, 'provider': 'kakao',
                'local_path': local_path, 'image_url': raw,
                'gallery_url': f"https://place.map.kakao.com/{provider_id}#photo/{photo_id}",
                'photo_id': photo_id, 'photo_type': ph.get('type', ''),
                'tags': [ph.get('type')] if ph.get('type') else [],
                'registered_at': ph.get('registered_at', ''),
                **img_meta,
            })
            idx += 1
            new_downloads += 1
            if local_path not in all_local:
                all_local.append(local_path)
            continue

        try:
            check_disk_limit()
        except DiskLimitExceeded as e:
            log.warning(str(e))
            signal.alarm(0)
            break

        img_bytes = download_bytes(session, resolved)
        if img_bytes is None:
            failed += 1
            time.sleep(DELAY_BETWEEN_IMGS)
            continue

        try:
            _, size_meta = save_image(img_bytes, save_path)
        except OSError as e:
            log.warning(f"  save_image failed, skipping DB insert: {e}")
            failed += 1
            time.sleep(DELAY_BETWEEN_IMGS)
            continue
        img_meta = extract_image_meta(img_bytes)   # EXIF from original bytes
        img_meta.update(size_meta)                 # post-resize w/h/size override

        insert_image_row(dbc, {
            'cafe_id': cafe_id, 'provider': 'kakao',
            'local_path': local_path, 'image_url': raw,
            'gallery_url': f"https://place.map.kakao.com/{provider_id}#photo/{photo_id}",
            'photo_id': photo_id, 'photo_type': ph.get('type', ''),
            'tags': [ph.get('type')] if ph.get('type') else [],
            'registered_at': ph.get('registered_at', ''),
            **img_meta,
        })
        idx += 1
        new_downloads += 1
        if local_path not in all_local:
            all_local.append(local_path)
        time.sleep(DELAY_BETWEEN_IMGS)

    # Persist cafe metadata
    metadata['local_images'] = all_local
    if page == 1 and total:
        metadata['all_photos'] = total
        metadata['photo_counts'] = counts
    metadata['scraped_photos'] = db_count + new_downloads
    dbc.execute('UPDATE scraped_cafes SET metadata=? WHERE id=?',
                (json.dumps(metadata, ensure_ascii=False), cafe_id))

    log.info(f"  {cafe_id} p{page}: +{new_downloads} new, {failed} failed, has_next={has_next}")
    return new_downloads, has_next, total


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cafe-id', type=str, help='Process only this cafe until exhausted')
    parser.add_argument('--force', action='store_true', help='Re-download already-saved images')
    args = parser.parse_args()

    dbc = DBClient()
    pending = init_scrape_state(dbc)
    if pending == 0:
        log.info("All scraped_cafes exhausted or flagged — nothing to do.")
        dbc.close()
        return

    session, current_ua = make_session()
    log.info(f"Session UA: {current_ua[:60]}…")

    pages_fetched = 0

    while not _shutdown.is_set():
        # ── Pick next cafe ────────────────────────────────────────────────────
        if args.cafe_id:
            row = dbc.fetchone('''
                SELECT s.cafe_id, s.next_page, s.attempt_count, c.provider_id, c.metadata
                FROM   kakao_scrape_state s
                JOIN   scraped_cafes c ON c.id = s.cafe_id
                WHERE  s.cafe_id = ? AND s.status = 'pending'
            ''', (args.cafe_id,))
        else:
            row = pick_random_pending_cafe(dbc)

        if row is None:
            log.info("No pending scraped_cafes — done.")
            break

        cafe_id, next_page, attempt_count, provider_id, meta_json = row
        try:
            metadata = json.loads(meta_json) if meta_json else {}
        except json.JSONDecodeError:
            metadata = {}

        # ── Refresh session periodically ──────────────────────────────────────
        if pages_fetched > 0 and pages_fetched % SESSION_REFRESH_EVERY == 0:
            session.close()
            session, current_ua = make_session()
            log.info(f"Session refreshed at page {pages_fetched} — UA: {current_ua[:60]}…")

        log.info(f"Processing {cafe_id} page {next_page} (attempts={attempt_count})")
        dbc.execute("UPDATE kakao_scrape_state SET last_attempted=CURRENT_TIMESTAMP WHERE cafe_id=?",
                    (cafe_id,))

        # ── Fetch + download one page ─────────────────────────────────────────
        signal.alarm(PAGE_TIMEOUT_SECS)
        try:
            new_dl, has_next, total_p1 = process_one_page(
                dbc, session, cafe_id, provider_id, metadata, next_page, ua=current_ua
            )
        except CafeTimeout as e:
            signal.alarm(0)
            log.warning(f"  {cafe_id} page {next_page}: timeout — flagging")
            dbc.execute("UPDATE kakao_scrape_state SET status='flagged' WHERE cafe_id=?", (cafe_id,))
            pages_fetched += 1
            time.sleep(random.uniform(*DELAY_BETWEEN_CAFES))
            continue
        except PersistentRateLimit as e:
            signal.alarm(0)
            log.error(f"Persistent rate-limit — {e}. Exiting for systemd restart.")
            session.close(); dbc.close(); sys.exit(1)
        except DiskLimitExceeded as e:
            signal.alarm(0)
            log.warning(str(e))
            break
        except Exception as e:
            signal.alarm(0)
            log.error(f"Error {cafe_id} page {next_page}: {e}", exc_info=True)
            pages_fetched += 1
            time.sleep(random.uniform(*DELAY_BETWEEN_CAFES))
            continue
        finally:
            signal.alarm(0)

        pages_fetched += 1

        # ── Update scrape state ───────────────────────────────────────────────
        if not has_next:
            if next_page == 1 and total_p1 == 0:
                new_attempts = attempt_count + 1
                if new_attempts >= MAX_ATTEMPT_COUNT:
                    log.info(f"  {cafe_id}: flagged after {new_attempts} zero-result attempts")
                    dbc.execute(
                        "UPDATE kakao_scrape_state SET status='flagged', attempt_count=? WHERE cafe_id=?",
                        (new_attempts, cafe_id))
                else:
                    log.info(f"  {cafe_id}: zero results, attempt {new_attempts}/{MAX_ATTEMPT_COUNT}")
                    dbc.execute(
                        "UPDATE kakao_scrape_state SET attempt_count=? WHERE cafe_id=?",
                        (new_attempts, cafe_id))
            else:
                log.info(f"  {cafe_id}: exhausted (page {next_page} was last)")
                dbc.execute(
                    "UPDATE kakao_scrape_state SET status='exhausted', next_page=? WHERE cafe_id=?",
                    (next_page + 1, cafe_id))
        else:
            dbc.execute(
                "UPDATE kakao_scrape_state SET next_page=?, attempt_count=0 WHERE cafe_id=?",
                (next_page + 1, cafe_id))

        if pages_fetched % 50 == 0:
            pending = dbc.fetchval("SELECT COUNT(*) FROM kakao_scrape_state WHERE status='pending'")
            log.info(f"Progress: {pages_fetched} pages fetched, {pending} scraped_cafes still pending")

        time.sleep(random.uniform(*DELAY_BETWEEN_CAFES))

    session.close()
    dbc.close()
    log.info("Done.")


if __name__ == '__main__':
    main()
