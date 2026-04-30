#!/usr/bin/env python3
"""
disk_import_kakao_images.py
============================
# Run once: 2026-04-30. Purpose: Restore scraped.db image rows from files already on
# disk after bulk delete of bad-backfill rows. Files ARE correctly associated to cafes
# (each lives in /kakao/{provider_id}/images/). Only photo_id and image_url are unknown.
#
# Inserts rows with photo_id=NULL (cafe association correct, photo_id unknown — scraper
# can fill these in later if needed). Sets kakao_scrape_state to 'exhausted' for each
# imported cafe so the scraper does not redundantly re-download.
#
# Idempotent: skips cafes that already have image rows in scraped.db.

Usage:
    cd scraper && source ../venv/bin/activate
    python tools/disk_import_kakao_images.py --dry-run         # scope only, no writes
    python tools/disk_import_kakao_images.py --limit 5         # test on 5 cafes
    python tools/disk_import_kakao_images.py                   # run for all
"""

import argparse
import json
import logging
import os
import sqlite3
import struct
import sys
from io import BytesIO
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRAPER_DIR = _HERE.parent
sys.path.insert(0, str(_SCRAPER_DIR / 'lib'))

from utils import DATA_DIR, DB_PATH
from db_client import DBClient
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

KAKAO_DIR = Path(DATA_DIR) / 'kakao'
BATCH_SIZE = 500  # images per executemany call


def _gps_rational(val):
    try:
        return float(val[0]) + float(val[1]) / 60.0 + float(val[2]) / 3600.0
    except Exception:
        return None


def extract_meta(path: Path) -> dict:
    """Read JPEG from disk, return dimensions + file_size + EXIF GPS/date."""
    meta = {'width': None, 'height': None, 'file_size': 0,
            'exif_date': None, 'exif_lat': None, 'exif_lon': None}
    try:
        data = path.read_bytes()
        meta['file_size'] = len(data)
        img = Image.open(BytesIO(data))
        meta['width'], meta['height'] = img.size

        raw_exif = img._getexif()
        if not raw_exif:
            return meta
        named = {TAGS.get(k, k): v for k, v in raw_exif.items()}

        for tag in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
            if tag in named:
                try:
                    meta['exif_date'] = named[tag].replace(':', '-', 2)
                except Exception:
                    pass
                break

        gps = named.get('GPSInfo')
        if gps:
            g = {GPSTAGS.get(k, k): v for k, v in gps.items()}
            lat_v, lat_r = g.get('GPSLatitude'), g.get('GPSLatitudeRef', 'N')
            lon_v, lon_r = g.get('GPSLongitude'), g.get('GPSLongitudeRef', 'E')
            if lat_v and lon_v:
                lat = _gps_rational(lat_v)
                lon = _gps_rational(lon_v)
                if lat is not None and lon is not None:
                    meta['exif_lat'] = lat if lat_r == 'N' else -lat
                    meta['exif_lon'] = lon if lon_r == 'E' else -lon
    except Exception:
        meta['file_size'] = path.stat().st_size if path.exists() else 0
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Report scope only, no writes')
    parser.add_argument('--limit', type=int, default=0, help='Process only N cafe dirs (0=all)')
    args = parser.parse_args()

    # Read-only snapshot of current DB state (safe alongside running db_server in WAL mode)
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)

    already_imported = set(
        r[0] for r in conn.execute(
            "SELECT DISTINCT cafe_id FROM images WHERE provider='kakao'"
        )
    )
    log.info(f"Cafes already have kakao images in scraped.db: {len(already_imported)}")

    # belongs_to_cafe_id lookup: cafe_id → belongs_to
    belongs_map = {
        r[0]: r[1] for r in conn.execute(
            "SELECT id, belongs_to_cafe_id FROM scraped_cafes WHERE provider='kakao'"
        )
    }
    log.info(f"Known kakao scraped_cafes: {len(belongs_map)}")
    conn.close()

    cafe_dirs = sorted(d for d in KAKAO_DIR.iterdir() if d.is_dir())
    log.info(f"Kakao dirs on disk: {len(cafe_dirs)}")
    if args.limit:
        cafe_dirs = cafe_dirs[:args.limit]

    dbc = None if args.dry_run else DBClient()

    total_cafes = 0
    total_images = 0
    skipped_existing = 0
    skipped_no_cafe = 0

    batch: list[tuple] = []

    def flush_batch():
        nonlocal total_images
        if not batch or args.dry_run:
            total_images += len(batch)
            batch.clear()
            return
        dbc.executemany('''
            INSERT OR IGNORE INTO images
              (cafe_id, provider, local_path, image_url, gallery_url,
               photo_id, photo_type, tags, registered_at,
               width, height, file_size, exif_date, exif_lat, exif_lon,
               belongs_to_cafe_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', batch)
        total_images += len(batch)
        batch.clear()

    for cafe_dir in cafe_dirs:
        safe_id = cafe_dir.name
        cafe_id = f'kakao_{safe_id}'

        if cafe_id in already_imported:
            skipped_existing += 1
            continue

        if cafe_id not in belongs_map:
            skipped_no_cafe += 1
            continue

        img_dir = cafe_dir / 'images'
        if not img_dir.exists():
            continue

        jpegs = sorted(img_dir.glob('*.jpg'))
        if not jpegs:
            continue

        belongs_to = belongs_map[cafe_id]
        cafe_rows = 0

        for jpg in jpegs:
            if jpg.stat().st_size == 0:
                continue
            meta = extract_meta(jpg)
            if meta['file_size'] == 0:
                continue
            local_path = f'/images/kakao/{safe_id}/images/{jpg.name}'
            batch.append((
                cafe_id, 'kakao', local_path,
                None, None,          # image_url, gallery_url
                None, None,          # photo_id, photo_type
                '[]', None,          # tags (empty JSON array), registered_at
                meta['width'], meta['height'], meta['file_size'],
                meta['exif_date'], meta['exif_lat'], meta['exif_lon'],
                belongs_to,
            ))
            cafe_rows += 1

        if not args.dry_run and cafe_rows > 0:
            dbc.execute('''
                UPDATE kakao_scrape_state
                SET status = 'exhausted', next_page = 999,
                    last_attempted = CURRENT_TIMESTAMP
                WHERE cafe_id = ? AND status = 'pending'
            ''', (cafe_id,))

        if len(batch) >= BATCH_SIZE:
            flush_batch()

        total_cafes += 1

        if args.dry_run and total_cafes <= 3:
            log.info(f"  DRY-RUN sample {cafe_id}: {cafe_rows} files")

        if total_cafes % 1000 == 0:
            flush_batch()
            log.info(f"Progress: {total_cafes} cafes, {total_images} images so far")

    flush_batch()

    log.info(
        f"{'DRY-RUN ' if args.dry_run else ''}Done.\n"
        f"  Cafes processed : {total_cafes}\n"
        f"  Images inserted : {total_images}\n"
        f"  Skipped (in DB) : {skipped_existing}\n"
        f"  Skipped (no DB cafe row): {skipped_no_cafe}"
    )


if __name__ == '__main__':
    main()
