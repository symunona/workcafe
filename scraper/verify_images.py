#!/usr/bin/env python3
"""
Verify images table against disk. Sets file_size=0 for rows whose local_path
doesn't exist on disk. Run periodically or after disk moves.
"""
import os, sqlite3

DB = '../data/seoul/cafedata.db'
DATA_DIR = '../data/seoul'

os.chdir(os.path.dirname(os.path.abspath(__file__)))
conn = sqlite3.connect(DB)

total = missing = 0
ids_to_clear = []

for (row_id, path) in conn.execute(
    "SELECT id, local_path FROM images WHERE local_path IS NOT NULL AND file_size > 0"
):
    total += 1
    disk = os.path.join(DATA_DIR, path.replace('/images/', '', 1))
    if not os.path.exists(disk):
        ids_to_clear.append(row_id)
        missing += 1

if ids_to_clear:
    conn.executemany("UPDATE images SET file_size=0 WHERE id=?", [(i,) for i in ids_to_clear])
    conn.commit()

conn.close()
print(f"Checked {total} rows. Cleared file_size on {missing} missing files.")
