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
import sqlite3
import struct
from urllib.parse import urlparse, unquote, parse_qs, quote
from io import BytesIO

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

import requests
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from utils import DB_PATH, DATA_DIR, get_db_conn, db_execute, flush_db_queue, normalize_provider_id
from disk_check import check_disk_limit, DiskLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_kakao_images_v3.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

PHOTOS_API  = "https://place-api.map.kakao.com/places/tab/photos/{place_id}?page={page}"
CTHUMB_BASE = "https://img1.kakaocdn.net/cthumb/local/C800x800.q50/?fname="
MAX_PAGES   = 500
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
DELAY_BETWEEN_IMGS  = 0.2


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

def fetch_photo_pages(session, place_id: str):
    """
    Yields dicts: {url, resolved_url, photo_id, photo_type, registered_at, gallery_url}
    Also returns (total, counts) via a mutable holder.
    """
    page = 1
    total = 0
    counts = {}

    while page <= MAX_PAGES:
        api_url = PHOTOS_API.format(place_id=place_id, page=page)
        try:
            resp = session.get(api_url, headers=KAKAO_HEADERS, timeout=10)
            if resp.status_code == 404:
                break
            if resp.status_code == 429:
                log.warning("  Rate limited, sleeping 30s")
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
            log.info(f"  total={total} counts={counts}")

        photos = data.get('photos', [])
        if not photos:
            break

        for ph in photos:
            raw = ph.get('url', '')
            if not raw:
                continue
            photo_id = str(ph.get('photo_id', ''))
            yield {
                'image_url': raw,
                'resolved_url': resolve_url(raw),
                'photo_id': photo_id,
                'photo_type': ph.get('type', ''),
                'registered_at': ph.get('registered_at', ''),
                'gallery_url': f"https://place.map.kakao.com/{place_id}#photo/{photo_id}" if photo_id else f"https://place.map.kakao.com/{place_id}#photoview",
            }, total, counts

        if not data.get('has_next', False):
            break
        page += 1
        time.sleep(DELAY_BETWEEN_PAGES)


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

def image_row_exists(conn, cafe_id: str, photo_id: str) -> bool:
    if not photo_id:
        return False
    c = conn.cursor()
    c.execute('SELECT 1 FROM images WHERE cafe_id=? AND photo_id=?', (cafe_id, photo_id))
    return c.fetchone() is not None


def insert_image_row(conn, row: dict):
    conn.execute('''
        INSERT OR REPLACE INTO images
          (cafe_id, provider, local_path, image_url, gallery_url,
           photo_id, photo_type, tags, registered_at,
           width, height, file_size, exif_date, exif_lat, exif_lon)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        row['cafe_id'], row['provider'], row.get('local_path'), row.get('image_url'),
        row.get('gallery_url'), row.get('photo_id'), row.get('photo_type'),
        json.dumps(row.get('tags', []), ensure_ascii=False),
        row.get('registered_at'),
        row.get('width'), row.get('height'), row.get('file_size'),
        row.get('exif_date'), row.get('exif_lat'), row.get('exif_lon'),
    ))
    conn.commit()


# ── Main per-cafe processing ─────────────────────────────────────────────────

def process_cafe(conn, session, cafe_id, provider_id, metadata, force=False):
    safe_id = normalize_provider_id(provider_id)
    img_dir = os.path.join(DATA_DIR, 'kakao', safe_id, 'images')

    existing_files = set(
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
    ) if os.path.exists(img_dir) else set()

    if existing_files and not force:
        log.info(f"  Skip {cafe_id}: {len(existing_files)} files already on disk")
        return len(existing_files)

    log.info(f"Processing {cafe_id}")
    os.makedirs(img_dir, exist_ok=True)

    all_local = list(metadata.get('local_images', []))
    downloaded = 0
    failed = 0
    total = 0
    counts = {}

    seen_urls = set()
    photo_gen = fetch_photo_pages(session, provider_id)

    for item_tuple in photo_gen:
        photo_info, total, counts = item_tuple

        resolved = photo_info['resolved_url']
        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)

        idx = len(seen_urls) - 1
        ext = os.path.splitext(urlparse(resolved.split('?')[0]).path)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            ext = '.jpg'
        fname = f"photo_{idx:04d}{ext}"
        save_path = os.path.join(img_dir, fname)
        local_path = f"/images/kakao/{safe_id}/images/{fname}"

        # Skip if file exists and DB row exists
        if os.path.exists(save_path) and not force:
            if not image_row_exists(conn, cafe_id, photo_info['photo_id']):
                # File exists but no DB row — add the row from disk
                img_meta = extract_image_meta(open(save_path, 'rb').read())
                insert_image_row(conn, {
                    'cafe_id': cafe_id, 'provider': 'kakao',
                    'local_path': local_path,
                    'image_url': photo_info['image_url'],
                    'gallery_url': photo_info['gallery_url'],
                    'photo_id': photo_info['photo_id'],
                    'photo_type': photo_info['photo_type'],
                    'tags': [photo_info['photo_type']] if photo_info['photo_type'] else [],
                    'registered_at': photo_info['registered_at'],
                    **img_meta,
                })
            downloaded += 1
            if local_path not in all_local:
                all_local.append(local_path)
            continue

        try:
            check_disk_limit()
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break

        img_bytes = download_bytes(session, resolved)
        if img_bytes is None:
            failed += 1
            time.sleep(DELAY_BETWEEN_IMGS)
            continue

        with open(save_path, 'wb') as f:
            f.write(img_bytes)

        img_meta = extract_image_meta(img_bytes)
        insert_image_row(conn, {
            'cafe_id': cafe_id, 'provider': 'kakao',
            'local_path': local_path,
            'image_url': photo_info['image_url'],
            'gallery_url': photo_info['gallery_url'],
            'photo_id': photo_info['photo_id'],
            'photo_type': photo_info['photo_type'],
            'tags': [photo_info['photo_type']] if photo_info['photo_type'] else [],
            'registered_at': photo_info['registered_at'],
            **img_meta,
        })

        downloaded += 1
        if local_path not in all_local:
            all_local.append(local_path)
        time.sleep(DELAY_BETWEEN_IMGS)

    # Update cafe metadata
    metadata['local_images'] = all_local
    metadata['all_photos'] = total
    metadata['scraped_photos'] = downloaded
    metadata['photo_counts'] = counts
    db_execute(conn, 'UPDATE cafes SET metadata=? WHERE id=?',
               (json.dumps(metadata, ensure_ascii=False), cafe_id))
    flush_db_queue(conn)

    log.info(f"  {cafe_id}: {downloaded} saved, {failed} failed / {total} total")
    return downloaded


# ── Entry point ──────────────────────────────────────────────────────────────

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

    log.info(f"Processing {len(rows)} Kakao cafes")
    session = requests.Session()
    session.headers.update(IMG_HEADERS)

    for i, (cafe_id, provider_id, meta_json) in enumerate(rows):
        try:
            metadata = json.loads(meta_json) if meta_json else {}
        except json.JSONDecodeError:
            metadata = {}

        try:
            process_cafe(conn, session, cafe_id, provider_id, metadata, force=args.force)
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break
        except Exception as e:
            log.error(f"Error {cafe_id}: {e}", exc_info=True)

        time.sleep(random.uniform(*DELAY_BETWEEN_CAFES))

        if (i + 1) % 20 == 0:
            log.info(f"Progress: {i+1}/{len(rows)} cafes")

    conn.close()
    log.info("Done.")


if __name__ == '__main__':
    main()
