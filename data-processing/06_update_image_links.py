#!/usr/bin/env python3
"""
06_update_image_links.py — Bulk update images.belongs_to_cafe_id.

Faster than per-cafe updates in main pipeline.
Run after 04_normalize_pipeline.py completes.
"""
import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper'))

from db_client import DBClient

parser = argparse.ArgumentParser()
parser.add_argument("--socket", default="/tmp/workcafe_db.sock", help="Unix socket path")
args = parser.parse_args()

dbc = DBClient(socket_path=args.socket)

before = dbc.fetchval("SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NOT NULL")
print(f"Images with belongs_to_cafe_id before: {before}")

# Bulk update from scraped_cafes table
dbc.execute("""
    UPDATE images
    SET belongs_to_cafe_id = (
        SELECT belongs_to_cafe_id FROM scraped_cafes WHERE scraped_cafes.id = images.cafe_id
    )
    WHERE belongs_to_cafe_id IS NULL
      AND cafe_id IN (SELECT id FROM scraped_cafes WHERE belongs_to_cafe_id IS NOT NULL)
""")

after = dbc.fetchval("SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NOT NULL")
print(f"Images with belongs_to_cafe_id after: {after}")
print(f"Updated: {after - before}")
print(f"Still missing: {dbc.fetchval('SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NULL AND cafe_id IS NOT NULL')}")
