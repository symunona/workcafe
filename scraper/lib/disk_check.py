import os
import logging
import shutil

# Absolute path to the data root being monitored
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_THIS_DIR, '..', 'data', 'seoul')
DATA_DIR = os.path.normpath(DATA_DIR)

MIN_FREE_GB = 4.0
MIN_FREE_BYTES = MIN_FREE_GB * 1024 ** 3


class DiskLimitExceeded(Exception):
    pass


def check_disk_limit(data_dir: str = DATA_DIR) -> float:
    """
    Returns current free space in GB.
    Raises DiskLimitExceeded if free space <= 4 GB.
    """
    total, used, free = shutil.disk_usage(data_dir)
    free_gb = free / (1024 ** 3)

    if free_gb <= MIN_FREE_GB:
        raise DiskLimitExceeded(
            f"Disk space critically low: {free_gb:.2f} GB remaining "
            f"(limit: {MIN_FREE_GB} GB). Stopping image download."
        )

    return free_gb


if __name__ == '__main__':
    try:
        free = check_disk_limit()
        print(f"Disk free space: {free:.2f} GB (minimum required: {MIN_FREE_GB} GB)")
    except DiskLimitExceeded as e:
        print(f"LIMIT REACHED: {e}")
