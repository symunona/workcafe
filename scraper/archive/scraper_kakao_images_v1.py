"""
scraper_kakao_images_v1.py
==========================
WHAT WORKS:
  - Direct REST API: place-api.map.kakao.com/places/tab/photos/{id}?page=N
    Returns has_next, counts.total, and per-photo URLs — no Playwright needed.
  - URL decoding: photos come as full-res direct URLs (t1.daumcdn.net/local/kakaomapPhoto/...)
    or blog CDN URLs (postfiles.pstatic.net). Both download fine with a regular session.
  - Stores all_photos (total count from API) and scraped_photos (actual downloaded) in metadata.
  - Disk limit guard via disk_check.py.

WHAT DID NOT WORK / LIMITATIONS:
  - The old scraper_images.py Kakao path used Playwright + CSS selectors (.photo_area img etc.)
    which only caught whatever was visible on the first page load — max ~15 images.
  - v1 initially got 406 on every API call. Root cause: Kakao requires two custom headers:
      pf: MW  (identifies mobile web client)
      appversion: 6.6.0
    Without these, requests returns 406 regardless of User-Agent or cookies.
  - Blog photos (type=BLOG) have naver CDN URLs that may expire or require referrer.
  - No Tor used here intentionally: the API is public and rate limits are lenient at 1 req/s.
    If you hit 429s, switch to get_tor_session() from utils.py.

Usage:
    cd scraper && source ../venv/bin/activate
    python scraper_kakao_images_v1.py [--limit N] [--cafe-id kakao_XXXXX]

    --limit N         max scraped_cafes to process (default: all)
    --cafe-id ID      process a single cafe by its global ID (e.g. kakao_21340017)
    --skip-existing   skip scraped_cafes that already have downloaded images (default: True)
    --force           re-download even if images already exist
"""

import os
import sys
import json
import time
import random
import logging
import argparse
import sqlite3
import requests
from urllib.parse import urlparse, unquote, parse_qs

# ── paths ──────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

from utils import DB_PATH, DATA_DIR, get_db_conn, db_execute, flush_db_queue, normalize_provider_id
from disk_check import check_disk_limit, DiskLimitExceeded

# ── logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_kakao_images_v1.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────
PHOTOS_API = "https://place-api.map.kakao.com/places/tab/photos/{place_id}?page={page}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
    "Referer": "https://place.map.kakao.com/",
    "Accept": "application/json, text/plain, */*",
    "pf": "MW",           # Required by Kakao API — identifies mobile web client
    "appversion": "6.6.0",  # Required by Kakao API — without these two headers → 406
}
PAGE_SIZE_GUESS = 20      # API returns ~20 photos per page
MAX_PAGES = 50            # safety cap → 1000 photos max
DELAY_BETWEEN_PAGES = 1.0 # seconds
DELAY_BETWEEN_CAFES = (1.5, 3.0)
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def decode_kakao_img_url(url: str) -> str:
    """
    Kakao serves thumbnails via cthumb proxy:
        https://img1.kakaocdn.net/cthumb/local/C196x196.q50/?fname=<encoded_original_url>
    This extracts and returns the original full-res URL.
    For non-cthumb URLs (daumcdn, postfiles, etc.) returns the URL unchanged,
    but ensures ?original is appended for daumcdn kakaomapPhoto URLs.
    """
    if 'cthumb' in url and 'fname=' in url:
        try:
            qs = parse_qs(urlparse(url).query)
            if 'fname' in qs:
                url = unquote(qs['fname'][0])
        except Exception:
            pass

    # Prefer original resolution for daumcdn hosted photos
    if 't1.daumcdn.net/local/kakaomapPhoto' in url:
        if '?original' not in url and '?type=' not in url:
            url = url + '?original'
        elif '?type=' in url:
            url = url.split('?')[0] + '?original'

    return url


def fetch_all_photo_urls(session: requests.Session, place_id: str):
    """
    Paginate through the Kakao photos API and return:
        (all_urls: list[str], total_count: int, counts_meta: dict)
    """
    all_urls = []
    total_count = 0
    counts_meta = {}
    page = 1

    while page <= MAX_PAGES:
        url = PHOTOS_API.format(place_id=place_id, page=page)
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 404:
                log.warning(f"  Place {place_id} not found (404)")
                break
            if resp.status_code == 429:
                log.warning(f"  Rate limited on page {page}, sleeping 30s")
                time.sleep(30)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error(f"  API error page {page}: {e}")
            break

        if page == 1:
            counts_meta = data.get('counts', {})
            total_count = counts_meta.get('total', 0)
            log.info(f"  Total photos reported: {total_count}  breakdown: {counts_meta}")

        photos = data.get('photos', [])
        if not photos:
            break

        for photo in photos:
            raw_url = photo.get('url', '')
            if raw_url:
                clean = decode_kakao_img_url(raw_url)
                all_urls.append(clean)

        has_next = data.get('has_next', False)
        log.debug(f"  page {page}: got {len(photos)} photos, has_next={has_next}")

        if not has_next:
            break

        page += 1
        time.sleep(DELAY_BETWEEN_PAGES)

    # Deduplicate preserving order
    seen = set()
    deduped = []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)

    return deduped, total_count, counts_meta


def download_image(session, url, save_path):
    try:
        resp = session.get(url, stream=True, timeout=15)
        resp.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        log.error(f"    Download failed {url}: {e}")
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
        return False


def process_cafe(conn, session, cafe_id, provider_id, metadata, force=False):
    safe_id = normalize_provider_id(provider_id)
    img_dir = os.path.join(DATA_DIR, 'kakao', safe_id, 'images')

    # Check if already done
    existing = [
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
    ] if os.path.exists(img_dir) else []

    if existing and not force:
        # Update counts if missing
        if 'all_photos' not in metadata or 'scraped_photos' not in metadata:
            metadata['scraped_photos'] = len(existing)
            db_execute(conn,
                'UPDATE scraped_cafes SET metadata=? WHERE id=?',
                (json.dumps(metadata, ensure_ascii=False), cafe_id)
            )
        log.info(f"  Skipping {cafe_id}: already has {len(existing)} images")
        return len(existing)

    log.info(f"Processing {cafe_id} (provider_id={provider_id})")
    photo_urls, total_count, counts_meta = fetch_all_photo_urls(session, provider_id)

    if not photo_urls:
        log.info(f"  No photos found for {cafe_id}")
        metadata['all_photos'] = total_count
        metadata['scraped_photos'] = 0
        db_execute(conn,
            'UPDATE scraped_cafes SET metadata=? WHERE id=?',
            (json.dumps(metadata, ensure_ascii=False), cafe_id)
        )
        return 0

    os.makedirs(img_dir, exist_ok=True)
    downloaded = 0

    for idx, url in enumerate(photo_urls):
        ext = os.path.splitext(urlparse(url).path)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            ext = '.jpg'
        fname = f"photo_{idx:04d}{ext}"
        save_path = os.path.join(img_dir, fname)

        if os.path.exists(save_path) and not force:
            downloaded += 1
            continue

        try:
            check_disk_limit()
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break

        if download_image(session, url, save_path):
            downloaded += 1
            time.sleep(0.3)

    # Build local_images list
    all_files = sorted(
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
    )
    local_paths = [f"/images/kakao/{safe_id}/images/{f}" for f in all_files]

    metadata['local_images'] = local_paths
    metadata['all_photos'] = total_count
    metadata['scraped_photos'] = len(all_files)
    metadata['photo_counts'] = counts_meta

    db_execute(conn,
        'UPDATE scraped_cafes SET metadata=? WHERE id=?',
        (json.dumps(metadata, ensure_ascii=False), cafe_id)
    )
    log.info(f"  Done: {len(all_files)} downloaded / {total_count} total for {cafe_id}")
    return len(all_files)


def main():
    parser = argparse.ArgumentParser(description="Kakao image scraper v1 — full pagination via REST API")
    parser.add_argument('--limit', type=int, default=0, help="Max scraped_cafes to process (0=all)")
    parser.add_argument('--cafe-id', type=str, help="Process a single cafe by global id")
    parser.add_argument('--force', action='store_true', help="Re-download even if images exist")
    args = parser.parse_args()

    conn = get_db_conn()
    cursor = conn.cursor()

    if args.cafe_id:
        cursor.execute('SELECT id, provider_id, metadata FROM scraped_cafes WHERE id=? AND provider=?',
                       (args.cafe_id, 'kakao'))
    else:
        # Prioritise scraped_cafes with zero or few images
        cursor.execute('''
            SELECT id, provider_id, metadata FROM scraped_cafes
            WHERE provider = 'kakao'
            ORDER BY
                CASE WHEN json_extract(metadata, '$.local_images') IS NULL THEN 0 ELSE 1 END ASC,
                json_array_length(COALESCE(json_extract(metadata, '$.local_images'), '[]')) ASC
        ''')

    rows = cursor.fetchall()
    if args.limit > 0:
        rows = rows[:args.limit]

    log.info(f"Processing {len(rows)} Kakao scraped_cafes")
    session = make_session()
    total_downloaded = 0

    for i, (cafe_id, provider_id, metadata_json) in enumerate(rows):
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except json.JSONDecodeError:
            metadata = {}

        try:
            n = process_cafe(conn, session, cafe_id, provider_id, metadata, force=args.force)
            total_downloaded += n
            flush_db_queue(conn)
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break
        except Exception as e:
            log.error(f"Unexpected error for {cafe_id}: {e}", exc_info=True)

        delay = random.uniform(*DELAY_BETWEEN_CAFES)
        time.sleep(delay)

        if (i + 1) % 10 == 0:
            log.info(f"Progress: {i+1}/{len(rows)} scraped_cafes, {total_downloaded} images total")

    flush_db_queue(conn)
    conn.close()
    log.info(f"Done. Total images downloaded this run: {total_downloaded}")


if __name__ == '__main__':
    main()
