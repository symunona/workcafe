#!/usr/bin/env python3
"""
restore_google_image_rows.py
============================
Repair `scraped.db`.`images` for provider=google after the "file exists → skip the
INSERT" bug in scraper_google_images_v1.py (fixed 2026-07-14). That bug let ~54k
images sit on disk with no row in scraped.db.

The two databases are NOT interchangeable:
  * scraped.db — the scraper's ledger. Lost the rows (bug + an earlier bulk delete).
  * clean.db   — the serving DB. `00_sync_from_scraped.py` copies rows into it with
                 INSERT OR IGNORE keyed on the PRIMARY KEY `id`, and never prunes,
                 so it is a superset and still holds most of the lost rows *with
                 their original ids and full metadata* (image_url, photo_id).

That id-keyed sync is the trap: if we re-insert a known row into scraped.db under a
FRESH autoincrement id, the next sync will not recognise it and will add a SECOND
copy to clean.db. So rows that clean.db already knows must be restored under their
ORIGINAL id, which makes the sync a no-op for them.

Phase A — restore from clean.db (preferred): full-fidelity, id-preserving copy of
          rows whose local_path is missing from scraped.db. Keeps the real
          image_url/photo_id, which disk cannot reconstruct.
Phase B — disk import (fallback): only for files present on disk but absent from
          BOTH databases. photo_id is derivable for google (fname img_{idx} and
          photo_id f"{cafe_id}_{idx}" share the same index); image_url is not, so
          it is left NULL — same shape as the 2026-04-30 kakao disk import.
          Skips files whose content hash appears under more than one cafe: the
          pre-2026-07 extractor also scooped "similar places" carousel tiles
          belonging to OTHER cafes. (cafe_chains is empty, so shared content is not
          explained by legitimate chain sharing.) --include-ambiguous overrides.

Files on disk are never modified. Only rows are written, only into scraped.db.

Usage:
    cd scraper && source ../venv/bin/activate
    python tools/restore_google_image_rows.py --dry-run     # report scope, no writes
    python tools/restore_google_image_rows.py               # apply
"""

import argparse
import hashlib
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRAPER_DIR = _HERE.parent
sys.path.insert(0, str(_SCRAPER_DIR / 'lib'))

from utils import DATA_DIR, DB_PATH, normalize_provider_id
from db_client import DBClient
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

GOOGLE_DIR = Path(DATA_DIR) / 'google'
CLEAN_DB = Path(DB_PATH).parent / 'clean.db'
BATCH_SIZE = 500

FNAME_RE = re.compile(r'^img_(\d+)\.(jpg|jpeg|png|gif|webp)$', re.I)

COLS = ('id', 'cafe_id', 'provider', 'local_path', 'image_url', 'gallery_url',
        'photo_id', 'photo_type', 'tags', 'registered_at', 'width', 'height',
        'file_size', 'exif_date', 'exif_lat', 'exif_lon', 'belongs_to_cafe_id')


def _gps_rational(val):
    try:
        return float(val[0]) + float(val[1]) / 60.0 + float(val[2]) / 3600.0
    except Exception:
        return None


def extract_meta(data: bytes) -> dict:
    meta = {'width': None, 'height': None, 'file_size': len(data),
            'exif_date': None, 'exif_lat': None, 'exif_lon': None}
    try:
        img = Image.open(BytesIO(data))
        meta['width'], meta['height'] = img.size
        raw = img._getexif()
        if not raw:
            return meta
        named = {TAGS.get(k, k): v for k, v in raw.items()}
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
                lat, lon = _gps_rational(lat_v), _gps_rational(lon_v)
                if lat is not None and lon is not None:
                    meta['exif_lat'] = lat if lat_r == 'N' else -lat
                    meta['exif_lon'] = lon if lon_r == 'E' else -lon
    except Exception:
        pass
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='Report scope only, no writes')
    ap.add_argument('--include-ambiguous', action='store_true',
                    help='Phase B: also import files whose content appears under >1 cafe')
    ap.add_argument('--skip-disk', action='store_true', help='Run phase A only')
    args = ap.parse_args()

    s = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    c = sqlite3.connect(f'file:{CLEAN_DB}?mode=ro', uri=True)

    scraped_paths = {r[0] for r in s.execute(
        "SELECT local_path FROM images WHERE provider='google' AND local_path IS NOT NULL")}
    scraped_ids = {r[0] for r in s.execute("SELECT id FROM images")}
    scraped_photo_ids = {r[0] for r in s.execute(
        "SELECT photo_id FROM images WHERE provider='google' AND photo_id IS NOT NULL")}
    log.info(f"scraped.db: {len(scraped_paths)} google local_paths, {len(scraped_ids)} total ids")

    # files actually present on disk
    disk_paths = set()
    disk_file = {}
    for d in GOOGLE_DIR.iterdir():
        img_dir = d / 'images'
        if not img_dir.is_dir():
            continue
        for f in img_dir.iterdir():
            if FNAME_RE.match(f.name):
                lp = f'/images/google/{d.name}/images/{f.name}'
                disk_paths.add(lp)
                disk_file[lp] = f
    log.info(f"disk: {len(disk_paths)} google image files")

    # ── Phase A: restore from clean.db, id-preserving ─────────────────────────
    clean_rows = list(c.execute(
        f"SELECT {','.join(COLS)} FROM images WHERE provider='google'"))
    c.close()
    s.close()

    phase_a, a_skip_dangling, a_skip_idclash, a_skip_present = [], 0, 0, 0
    for row in clean_rows:
        rid, lp = row[0], row[3]
        if lp in scraped_paths:
            a_skip_present += 1
            continue
        if lp not in disk_paths:
            a_skip_dangling += 1          # clean.db row whose file is gone
            continue
        if rid in scraped_ids:
            a_skip_idclash += 1           # id taken by a different row — refuse to guess
            continue
        phase_a.append(row)
        scraped_paths.add(lp)
        scraped_ids.add(rid)
        if row[6]:
            scraped_photo_ids.add(row[6])

    log.info("─" * 60)
    log.info(f"PHASE A — restore from clean.db (full metadata, original ids)")
    log.info(f"  rows to restore        : {len(phase_a)}")
    log.info(f"  already in scraped.db  : {a_skip_present}")
    log.info(f"  file gone from disk    : {a_skip_dangling}")
    log.info(f"  id already taken       : {a_skip_idclash}")

    # ── Phase B: disk import for files in NEITHER db ──────────────────────────
    phase_b = []
    b_stats = defaultdict(int)
    if not args.skip_disk:
        missing = sorted(disk_paths - scraped_paths)

        # hash ALL google files to spot content living under >1 cafe
        h2dirs = defaultdict(set)
        fhash = {}
        for lp, f in disk_file.items():
            try:
                h = hashlib.md5(f.read_bytes()).hexdigest()
            except Exception:
                continue
            fhash[lp] = h
            h2dirs[h].add(f.parent.parent.name)
        leaked = {h for h, dirs in h2dirs.items() if len(dirs) > 1}

        # cafe_id lookup (normalize_provider_id is lossy → map forward from DB)
        s2 = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        safe_to_cafe, belongs = {}, {}
        for cafe_id, provider_id, bt in s2.execute(
                "SELECT id, provider_id, belongs_to_cafe_id FROM scraped_cafes WHERE provider='google'"):
            safe = normalize_provider_id(provider_id)
            if safe in safe_to_cafe and safe_to_cafe[safe] != cafe_id:
                safe_to_cafe[safe] = None
                continue
            safe_to_cafe[safe] = cafe_id
            belongs[cafe_id] = bt
        s2.close()

        for lp in missing:
            f = disk_file[lp]
            safe_id = f.parent.parent.name
            cafe_id = safe_to_cafe.get(safe_id)
            if not cafe_id:
                b_stats['skip_unknown_cafe'] += 1
                continue
            if fhash.get(lp) in leaked and not args.include_ambiguous:
                b_stats['skip_leaked'] += 1
                continue
            idx = int(FNAME_RE.match(f.name).group(1))
            photo_id = f'{cafe_id}_{idx}'
            if photo_id in scraped_photo_ids:
                b_stats['skip_photo_id_taken'] += 1
                continue
            try:
                data = f.read_bytes()
            except Exception:
                b_stats['skip_unreadable'] += 1
                continue
            if not data:
                b_stats['skip_empty'] += 1
                continue
            m = extract_meta(data)
            if not m['width']:
                b_stats['skip_not_image'] += 1
                continue
            phase_b.append((
                None, cafe_id, 'google', lp,
                None, None,            # image_url (unrecoverable), gallery_url
                photo_id, None,        # photo_id (derived), photo_type
                '[]', None,            # tags, registered_at
                m['width'], m['height'], m['file_size'],
                m['exif_date'], m['exif_lat'], m['exif_lon'],
                belongs.get(cafe_id),
            ))
            scraped_photo_ids.add(photo_id)
            b_stats['import'] += 1

        log.info("─" * 60)
        log.info(f"PHASE B — disk import (image_url unrecoverable → NULL)")
        log.info(f"  rows to insert         : {b_stats['import']}")
        log.info(f"  skipped, leaked >1 cafe: {b_stats['skip_leaked']}")
        log.info(f"  skipped, unknown cafe  : {b_stats['skip_unknown_cafe']}")
        log.info(f"  skipped, photo_id taken: {b_stats['skip_photo_id_taken']}")
        log.info(f"  skipped, bad/empty file: {b_stats['skip_empty'] + b_stats['skip_unreadable'] + b_stats['skip_not_image']}")

    total = len(phase_a) + len(phase_b)
    log.info("─" * 60)
    if args.dry_run:
        log.info(f"DRY RUN — nothing written. Would add {total} rows to scraped.db "
                 f"({len(phase_a)} restored w/ full metadata, {len(phase_b)} from disk).")
        return

    dbc = DBClient()
    ph = ','.join('?' * len(COLS))
    # Phase A keeps its original id; phase B lets AUTOINCREMENT assign one.
    for i in range(0, len(phase_a), BATCH_SIZE):
        dbc.executemany(f"INSERT OR IGNORE INTO images ({','.join(COLS)}) VALUES ({ph})",
                        phase_a[i:i + BATCH_SIZE])
    cols_b = COLS[1:]
    ph_b = ','.join('?' * len(cols_b))
    for i in range(0, len(phase_b), BATCH_SIZE):
        dbc.executemany(f"INSERT INTO images ({','.join(cols_b)}) VALUES ({ph_b})",
                        [r[1:] for r in phase_b[i:i + BATCH_SIZE]])
    dbc.close()
    log.info(f"DONE — inserted {total} rows into scraped.db "
             f"({len(phase_a)} restored, {len(phase_b)} from disk).")


if __name__ == '__main__':
    main()
