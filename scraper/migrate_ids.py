"""
One-time migration: rename existing provider directories to use
normalize_provider_id() names, then rebuild local_images in the DB.

Safe to re-run — already-normalized directories are skipped.
"""
import os
import json
import shutil
import logging
from utils import DATA_DIR, get_db_conn, normalize_provider_id

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)


def migrate():
    conn = get_db_conn()
    cursor = conn.cursor()

    cursor.execute('SELECT id, provider, provider_id FROM cafes')
    rows = cursor.fetchall()

    renamed = 0
    skipped = 0

    for cafe_id, provider, provider_id in rows:
        safe_id = normalize_provider_id(provider_id)
        provider_dir = os.path.join(DATA_DIR, provider)
        target_dir = os.path.join(provider_dir, safe_id)

        # Try to find the old directory: exact provider_id, then first 50 chars (old Google truncation)
        candidates = [
            os.path.join(provider_dir, provider_id),
            os.path.join(provider_dir, provider_id[:50]),
        ]
        old_dir = next((c for c in candidates if os.path.isdir(c) and c != target_dir), None)

        if os.path.isdir(target_dir):
            # Target already exists; remove any lingering old dir (scraper may have recreated it)
            if old_dir:
                shutil.rmtree(old_dir)
                logging.info(f"Removed stale dir {provider}/{os.path.basename(old_dir)} (target already exists)")
            skipped += 1
            continue

        if not old_dir:
            skipped += 1
            continue

        shutil.move(old_dir, target_dir)
        renamed += 1
        logging.info(f"Renamed {provider}/{os.path.basename(old_dir)} -> {safe_id}")

    conn.close()
    logging.info(f"Directories renamed: {renamed}, skipped: {skipped}")

    # Rebuild local_images now that dirs have canonical names
    logging.info("Rebuilding local_images in DB...")
    from sync_local_images import sync_local_images
    sync_local_images()


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    migrate()
