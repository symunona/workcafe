"""
compress_existing.py
====================
Re-encode existing scraped images to JPEG q75 / 1024px max.
Skips files already below SIZE_THRESHOLD_BYTES (default 100KB).
Updates local_path, width, height, file_size in the images table.
Also updates the local_images list in scraped_cafes.metadata for any renamed files.

Usage (from scraper/ dir):
    source ../venv/bin/activate
    python compress_existing.py [--threshold KB] [--workers N] [--dry-run]

Requires db_server NOT to be running (uses sqlite3 directly to avoid
socket overhead for bulk updates). Stop db_server before running this.
"""

import os
import sys
import json
import sqlite3
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

from utils import DATA_DIR, DB_PATH
from image_utils import save_image

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("log/compress_existing.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

DEFAULT_THRESHOLD_KB = 100
DEFAULT_WORKERS = 4


def local_path_to_disk(local_path: str) -> str:
    """
    /images/kakao/1234/images/photo.jpg  →  ../data/seoul/kakao/1234/images/photo.jpg
    Strips leading /images prefix then joins with DATA_DIR.
    """
    return DATA_DIR + local_path[len('/images'):]


def compress_one(row: tuple, threshold_bytes: int, dry_run: bool) -> dict | None:
    """
    Worker function. Returns a result dict on success/skip, None on hard error.
    result keys: image_id, old_local_path, new_local_path, width, height,
                 file_size, changed, skipped, reason
    """
    image_id, cafe_id, local_path = row

    if not local_path:
        return {'image_id': image_id, 'skipped': True, 'reason': 'no local_path'}

    disk_path = local_path_to_disk(local_path)

    if not os.path.exists(disk_path):
        return {'image_id': image_id, 'skipped': True, 'reason': 'file not found'}

    file_size = os.path.getsize(disk_path)
    if file_size < threshold_bytes:
        return {'image_id': image_id, 'skipped': True,
                'reason': f'below threshold ({file_size}B)'}

    if dry_run:
        return {
            'image_id': image_id, 'cafe_id': cafe_id,
            'old_local_path': local_path, 'new_local_path': local_path,
            'width': None, 'height': None, 'file_size': file_size,
            'changed': False, 'skipped': False, 'reason': 'dry-run',
        }

    try:
        img_bytes = open(disk_path, 'rb').read()
        actual_path, meta = save_image(img_bytes, disk_path)

        # If extension changed (e.g. .png → .jpg), delete the old file
        if actual_path != disk_path:
            try:
                os.remove(disk_path)
            except OSError:
                pass

        new_local_path = '/images' + actual_path[len(DATA_DIR):]
        changed = new_local_path != local_path or meta['file_size'] != file_size

        return {
            'image_id': image_id, 'cafe_id': cafe_id,
            'old_local_path': local_path, 'new_local_path': new_local_path,
            'width': meta['width'], 'height': meta['height'],
            'file_size': meta['file_size'],
            'changed': changed, 'skipped': False, 'reason': 'ok',
        }

    except Exception as e:
        log.error(f"  Error on {local_path}: {e}")
        return {'image_id': image_id, 'skipped': True, 'reason': f'error: {e}'}


def update_db(conn, results: list[dict]):
    """Apply all DB updates: images table + scraped_cafes metadata."""
    cur = conn.cursor()

    # Collect path renames per cafe for metadata update
    # { cafe_id: { old_path: new_path } }
    renames_by_cafe: dict[str, dict] = {}

    for r in results:
        if r.get('skipped') or not r.get('changed'):
            continue

        cur.execute(
            'UPDATE images SET local_path=?, width=?, height=?, file_size=? WHERE id=?',
            (r['new_local_path'], r['width'], r['height'], r['file_size'], r['image_id'])
        )

        if r['old_local_path'] != r['new_local_path']:
            cafe_id = r['cafe_id']
            if cafe_id not in renames_by_cafe:
                renames_by_cafe[cafe_id] = {}
            renames_by_cafe[cafe_id][r['old_local_path']] = r['new_local_path']

    # Update scraped_cafes.metadata local_images lists for renamed files
    for cafe_id, renames in renames_by_cafe.items():
        row = cur.execute(
            'SELECT metadata FROM scraped_cafes WHERE id=?', (cafe_id,)
        ).fetchone()
        if not row or not row[0]:
            continue
        try:
            meta = json.loads(row[0])
        except json.JSONDecodeError:
            continue

        local_images = meta.get('local_images', [])
        updated = [renames.get(p, p) for p in local_images]
        if updated != local_images:
            meta['local_images'] = updated
            cur.execute(
                'UPDATE scraped_cafes SET metadata=? WHERE id=?',
                (json.dumps(meta, ensure_ascii=False), cafe_id)
            )

    conn.commit()


def main():
    parser = argparse.ArgumentParser(description='Compress existing scraped images')
    parser.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD_KB,
                        help=f'Skip files smaller than this (KB, default {DEFAULT_THRESHOLD_KB})')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS,
                        help=f'Parallel workers (default {DEFAULT_WORKERS})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would change without writing files or DB')
    args = parser.parse_args()

    threshold_bytes = args.threshold * 1024
    log.info(f"Threshold: {args.threshold}KB | Workers: {args.workers} | Dry-run: {args.dry_run}")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=60000')

    rows = conn.execute(
        'SELECT id, cafe_id, local_path FROM images WHERE local_path IS NOT NULL'
    ).fetchall()
    log.info(f"Total image rows: {len(rows)}")

    results = []
    processed = skipped = changed = errors = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(compress_one, row, threshold_bytes, args.dry_run): row
            for row in rows
        }
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result is None:
                errors += 1
                continue

            results.append(result)

            if result.get('skipped'):
                skipped += 1
            else:
                processed += 1
                if result.get('changed'):
                    changed += 1

            if i % 1000 == 0:
                log.info(f"  {i}/{len(rows)} — processed:{processed} skipped:{skipped} changed:{changed}")

    log.info(f"Done. processed:{processed} skipped:{skipped} changed:{changed} errors:{errors}")

    if not args.dry_run and changed > 0:
        log.info(f"Writing {changed} DB updates…")
        update_db(conn, results)
        log.info("DB updated.")
    elif args.dry_run:
        to_change = [r for r in results if not r.get('skipped')]
        log.info(f"[dry-run] Would compress {len(to_change)} files")

    conn.close()


if __name__ == '__main__':
    main()
