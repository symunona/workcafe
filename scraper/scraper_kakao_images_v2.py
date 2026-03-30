"""
scraper_kakao_images_v2.py
==========================
WHAT WORKS (v1 → v2 fixes):
  - API: place-api.map.kakao.com/places/tab/photos/{id}?page=N with pf:MW + appversion:6.6.0
  - 30 photos/page, paginated via has_next flag
  - daumcdn (t1.daumcdn.net/local/kakaomapPhoto/...?original) — downloads fine directly
  - all_photos + scraped_photos metadata fields

WHAT CHANGED IN v2:
  - Naver Blog CDN (postfiles.pstatic.net) was returning 403 on direct download in v1.
    Fix: route through Kakao's cthumb proxy at C800x800.q50.
    Critical: keep the ?type=wNNN param in the encoded URL — stripping it breaks the proxy.
    Critical: only C800x800.q50 works; C1024x1024.q80 and higher sizes return 403.
  - Added per-type download tracking in photo_counts metadata.
  - Reduced delay for daumcdn (fast, no throttling), kept delay for blog CDN.

WHAT DID NOT WORK / LIMITATIONS:
  - Some blog photos (few %) still fail even through cthumb — likely expired links.
  - Very large cafes (>500 photos) take minutes — run as background service.
  - No Tor: API and cthumb don't appear to rate-limit at 1 cafe/3s. Add if you get 429s.

Usage:
    cd scraper && source ../venv/bin/activate
    python scraper_kakao_images_v2.py [--limit N] [--cafe-id kakao_XXXXX] [--force]
"""

import os
import sys
import json
import time
import random
import logging
import argparse
from urllib.parse import urlparse, unquote, parse_qs, quote

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

import requests
from utils import DB_PATH, DATA_DIR, get_db_conn, db_execute, flush_db_queue, normalize_provider_id
from disk_check import check_disk_limit, DiskLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_kakao_images_v2.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

PHOTOS_API   = "https://place-api.map.kakao.com/places/tab/photos/{place_id}?page={page}"
CTHUMB_BASE  = "https://img1.kakaocdn.net/cthumb/local/C800x800.q50/?fname="  # C1024 returns 403; C800 is max that works
MAX_PAGES    = 50
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

KAKAO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
    "Referer": "https://place.map.kakao.com/",
    "Accept": "application/json, text/plain, */*",
    "pf": "MW",
    "appversion": "6.6.0",
}

IMG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
    "Referer": "https://place.map.kakao.com/",
}

DELAY_BETWEEN_PAGES = 0.8
DELAY_BETWEEN_CAFES = (1.2, 2.5)


def make_session():
    s = requests.Session()
    s.headers.update(IMG_HEADERS)
    return s


def resolve_url(raw_url: str) -> str:
    """
    Convert a raw photo URL from the API to the best downloadable form:
      - cthumb proxy URL → decode to original
      - postfiles.pstatic.net (Naver blog CDN) → strip ?type= and proxy through cthumb
      - daumcdn kakaomapPhoto → keep, append ?original
      - everything else → pass through
    """
    # Decode existing cthumb wrappers first
    if 'cthumb' in raw_url and 'fname=' in raw_url:
        try:
            qs = parse_qs(urlparse(raw_url).query)
            if 'fname' in qs:
                raw_url = unquote(qs['fname'][0])
        except Exception:
            pass

    # Naver blog CDN: must go through Kakao's cthumb proxy.
    # IMPORTANT: keep the ?type=wNNN param — stripping it causes cthumb to return 403.
    # IMPORTANT: use C800x800.q50 — C1024 and higher return 403.
    if 'postfiles.pstatic.net' in raw_url or 'blogfiles.pstatic.net' in raw_url:
        return CTHUMB_BASE + quote(raw_url, safe='')

    # Kakao-hosted review photos: prefer ?original
    if 't1.daumcdn.net/local/kakaomapPhoto' in raw_url:
        base = raw_url.split('?')[0]
        return base + '?original'

    return raw_url


def fetch_all_photo_urls(session, place_id: str):
    """Returns (urls: list[str], total: int, counts: dict)"""
    all_urls = []
    total = 0
    counts = {}
    page = 1

    while page <= MAX_PAGES:
        url = PHOTOS_API.format(place_id=place_id, page=page)
        try:
            resp = session.get(url, headers=KAKAO_HEADERS, timeout=10)
            if resp.status_code == 404:
                log.warning(f"  404 for place {place_id}")
                break
            if resp.status_code == 429:
                log.warning(f"  429 rate-limited, sleeping 30s")
                time.sleep(30)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error(f"  API error page {page}: {e}")
            break

        if page == 1:
            counts = data.get('counts', {})
            total = counts.get('total', 0)
            log.info(f"  total={total} breakdown={counts}")

        photos = data.get('photos', [])
        if not photos:
            break

        for ph in photos:
            raw = ph.get('url', '')
            if raw:
                all_urls.append(resolve_url(raw))

        if not data.get('has_next', False):
            break

        page += 1
        time.sleep(DELAY_BETWEEN_PAGES)

    # Deduplicate preserving order
    seen = set()
    result = []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result, total, counts


def download_image(session, url, save_path) -> bool:
    try:
        resp = session.get(url, stream=True, timeout=15)
        resp.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        log.debug(f"    Failed {url[:80]}: {e}")
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
        return False


def process_cafe(conn, session, cafe_id, provider_id, metadata, force=False):
    safe_id = normalize_provider_id(provider_id)
    img_dir = os.path.join(DATA_DIR, 'kakao', safe_id, 'images')

    existing = [
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
    ] if os.path.exists(img_dir) else []

    if existing and not force:
        log.info(f"  Skip {cafe_id}: {len(existing)} images already on disk")
        return len(existing)

    log.info(f"Processing {cafe_id}")
    photo_urls, total, counts = fetch_all_photo_urls(session, provider_id)

    if not photo_urls:
        metadata['all_photos'] = total
        metadata['scraped_photos'] = 0
        db_execute(conn, 'UPDATE cafes SET metadata=? WHERE id=?',
                   (json.dumps(metadata, ensure_ascii=False), cafe_id))
        return 0

    os.makedirs(img_dir, exist_ok=True)
    downloaded = 0
    failed = 0

    for idx, url in enumerate(photo_urls):
        ext = os.path.splitext(urlparse(url.split('?')[0]).path)[1].lower()
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
        else:
            failed += 1

        time.sleep(0.2)

    all_files = sorted(
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
    )
    local_paths = [f"/images/kakao/{safe_id}/images/{f}" for f in all_files]

    metadata['local_images'] = local_paths
    metadata['all_photos'] = total
    metadata['scraped_photos'] = len(all_files)
    metadata['photo_counts'] = counts

    db_execute(conn, 'UPDATE cafes SET metadata=? WHERE id=?',
               (json.dumps(metadata, ensure_ascii=False), cafe_id))

    log.info(f"  {cafe_id}: {len(all_files)} saved, {failed} failed / {total} total")
    return len(all_files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--cafe-id', type=str)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    conn = get_db_conn()
    cursor = conn.cursor()

    if args.cafe_id:
        cursor.execute('SELECT id, provider_id, metadata FROM cafes WHERE id=? AND provider=?',
                       (args.cafe_id, 'kakao'))
    else:
        cursor.execute('''
            SELECT id, provider_id, metadata FROM cafes
            WHERE provider = 'kakao'
            ORDER BY
                CASE WHEN json_extract(metadata, '$.local_images') IS NULL THEN 0 ELSE 1 END ASC,
                json_array_length(COALESCE(json_extract(metadata, '$.local_images'), '[]')) ASC
        ''')

    rows = cursor.fetchall()
    if args.limit > 0:
        rows = rows[:args.limit]

    log.info(f"Processing {len(rows)} cafes")
    session = make_session()
    total_dl = 0

    for i, (cafe_id, provider_id, meta_json) in enumerate(rows):
        try:
            metadata = json.loads(meta_json) if meta_json else {}
        except json.JSONDecodeError:
            metadata = {}

        try:
            n = process_cafe(conn, session, cafe_id, provider_id, metadata, force=args.force)
            total_dl += n
            flush_db_queue(conn)
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break
        except Exception as e:
            log.error(f"Error {cafe_id}: {e}", exc_info=True)

        time.sleep(random.uniform(*DELAY_BETWEEN_CAFES))

        if (i + 1) % 10 == 0:
            log.info(f"Progress: {i+1}/{len(rows)} cafes processed, {total_dl} images total")

    flush_db_queue(conn)
    conn.close()
    log.info(f"Done. {total_dl} total images downloaded.")


if __name__ == '__main__':
    main()
