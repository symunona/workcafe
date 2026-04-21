"""
Rebuild metadata.local_images in the DB from what is actually on disk.

Iterates every row in the DB, computes the canonical directory name via
normalize_provider_id(), lists image files inside its images/ subdir, and
writes the correct local_images URL paths back to metadata.

Safe to re-run at any time (idempotent).
"""
import os
import json
import logging
from utils import DB_PATH, DATA_DIR, get_db_conn, normalize_provider_id, db_execute, flush_db_queue

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def sync_local_images():
    conn = get_db_conn()
    cursor = conn.cursor()

    cursor.execute('SELECT id, provider, provider_id, metadata FROM scraped_cafes')
    rows = cursor.fetchall()

    updated = 0
    skipped = 0

    for cafe_id, provider, provider_id, metadata_json in rows:
        safe_id = normalize_provider_id(provider_id)
        images_dir = os.path.join(DATA_DIR, provider, safe_id, 'images')

        if not os.path.isdir(images_dir):
            skipped += 1
            continue

        files = sorted(
            f for f in os.listdir(images_dir)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        )
        if not files:
            skipped += 1
            continue

        local_paths = [f'/images/{provider}/{safe_id}/images/{f}' for f in files]

        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        if metadata.get('local_images') == local_paths:
            skipped += 1
            continue

        metadata['local_images'] = local_paths
        db_execute(conn, 
            'UPDATE scraped_cafes SET metadata = ? WHERE id = ?',
            (json.dumps(metadata, ensure_ascii=False), cafe_id)
        )
        updated += 1
        logging.info(f"Updated {provider}/{safe_id}: {len(local_paths)} images")

    flush_db_queue(conn)
    conn.close()
    logging.info(f"Done. Updated: {updated}, Skipped/unchanged: {skipped}")


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sync_local_images()
