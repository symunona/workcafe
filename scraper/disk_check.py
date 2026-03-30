"""
Disk usage checker for image scrapers.
Call check_disk_limit() before downloading any image — it raises
DiskLimitExceeded if the data directory is at or above IMAGE_LIMIT_GB.

Usage:
    from disk_check import check_disk_limit, DiskLimitExceeded

    try:
        check_disk_limit()
    except DiskLimitExceeded as e:
        logging.warning(str(e))
        break  # stop scraping

Can also be run directly:
    python disk_check.py
"""

import os
import logging

# Absolute path to the data root being monitored
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_THIS_DIR, '..', 'data', 'seoul')
DATA_DIR = os.path.normpath(DATA_DIR)

IMAGE_LIMIT_GB = 40
IMAGE_LIMIT_BYTES = IMAGE_LIMIT_GB * 1024 ** 3


class DiskLimitExceeded(Exception):
    pass


def get_dir_size_bytes(path: str) -> int:
    """Walk the directory tree and sum file sizes. Skips symlinks."""
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for fname in files:
            fpath = os.path.join(dirpath, fname)
            if not os.path.islink(fpath):
                try:
                    total += os.path.getsize(fpath)
                except OSError:
                    pass
    return total


def check_disk_limit(data_dir: str = DATA_DIR) -> float:
    """
    Returns current usage in GB.
    Raises DiskLimitExceeded if at or above IMAGE_LIMIT_GB.
    """
    used_bytes = get_dir_size_bytes(data_dir)
    used_gb = used_bytes / 1024 ** 3

    if used_bytes >= IMAGE_LIMIT_BYTES:
        raise DiskLimitExceeded(
            f"Data directory has reached {used_gb:.2f} GB "
            f"(limit: {IMAGE_LIMIT_GB} GB). Stopping image download."
        )

    return used_gb


if __name__ == '__main__':
    try:
        used = check_disk_limit()
        print(f"Disk usage: {used:.2f} GB / {IMAGE_LIMIT_GB} GB ({used / IMAGE_LIMIT_GB * 100:.1f}%)")
    except DiskLimitExceeded as e:
        print(f"LIMIT REACHED: {e}")
