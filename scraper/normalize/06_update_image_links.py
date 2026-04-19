#!/usr/bin/env python3
"""
06_update_image_links.py — Bulk update images.belongs_to_cafe_id.

Faster than per-cafe updates in main pipeline.
Run after 04_normalize_pipeline.py completes.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))

from db_client import DBClient

dbc = DBClient()

before = dbc.fetchval("SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NOT NULL")
print(f"Images with belongs_to_cafe_id before: {before}")

# Bulk update from cafes table
dbc.execute("""
    UPDATE images
    SET belongs_to_cafe_id = (
        SELECT belongs_to_cafe_id FROM cafes WHERE cafes.id = images.cafe_id
    )
    WHERE belongs_to_cafe_id IS NULL
      AND cafe_id IN (SELECT id FROM cafes WHERE belongs_to_cafe_id IS NOT NULL)
""")

after = dbc.fetchval("SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NOT NULL")
print(f"Images with belongs_to_cafe_id after: {after}")
print(f"Updated: {after - before}")
print(f"Still missing: {dbc.fetchval('SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NULL AND cafe_id IS NOT NULL')}")
