#!/usr/bin/env python3
"""
create_subset.py — Extract a spatial subset of scraped.db for fast pipeline testing.

Creates a new SQLite file with the same schema, containing only scraped_cafes within
a square bounding box (blocksize × blocksize meters) centered on the given lat/lng.

belongs_to_cafe_id is reset to NULL so the pipeline runs fresh on the subset.

Usage:
    python3 scripts/create_subset.py --lat 37.492 --lng 126.989 --blocksize 1000 out.db
"""
import argparse
import math
import os
import shutil
import sqlite3
import sys
import tempfile


def lat_lon_bbox(lat, lon, half_m):
    dlat = half_m / 111000.0
    dlon = half_m / (111000.0 * math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lat",       type=float, required=True,  help="Center latitude")
    parser.add_argument("--lng",       type=float, required=True,  help="Center longitude")
    parser.add_argument("--blocksize", type=float, default=1000.0, help="Full side length in meters (default 1000)")
    parser.add_argument("--src",       default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "seoul", "scraped.db"
    ), help="Source scraped.db path")
    parser.add_argument("target", help="Output .db path")
    args = parser.parse_args()

    half = args.blocksize / 2.0
    min_lat, max_lat, min_lon, max_lon = lat_lon_bbox(args.lat, args.lng, half)

    print(f"Center:    ({args.lat}, {args.lng})")
    print(f"Block:     {args.blocksize:.0f}m × {args.blocksize:.0f}m")
    print(f"Bbox:      lat [{min_lat:.6f}, {max_lat:.6f}]  lon [{min_lon:.6f}, {max_lon:.6f}]")
    print(f"Source:    {args.src}")
    print(f"Target:    {args.target}")
    print()

    if not os.path.exists(args.src):
        print(f"ERROR: source not found: {args.src}", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(args.target):
        os.remove(args.target)

    # Copy full schema by cloning into empty DB via dump
    # Use ATTACH trick: create target with same schema, then INSERT filtered rows
    src = sqlite3.connect(args.src)
    src.row_factory = sqlite3.Row

    # Get IDs of matching scraped_cafes
    cafe_rows = src.execute(
        "SELECT * FROM scraped_cafes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (min_lat, max_lat, min_lon, max_lon)
    ).fetchall()

    if not cafe_rows:
        print("ERROR: no cafes found in bounding box", file=sys.stderr)
        sys.exit(1)

    cafe_ids = [r["id"] for r in cafe_rows]
    print(f"scraped_cafes in bbox: {len(cafe_rows)}")

    # Count by provider
    from collections import Counter
    by_prov = Counter(r["provider"] for r in cafe_rows)
    for prov, cnt in sorted(by_prov.items()):
        print(f"  {prov}: {cnt}")

    # Copy images for matching cafes
    placeholders = ",".join("?" * len(cafe_ids))
    image_rows = src.execute(
        f"SELECT * FROM images WHERE cafe_id IN ({placeholders})", cafe_ids
    ).fetchall()
    print(f"images:        {len(image_rows)}")

    # Create target with same schema (copy schema from source via iterdump trick)
    dst = sqlite3.connect(args.target)
    dst.execute("PRAGMA journal_mode=WAL")

    # Recreate schema from source
    schema_rows = src.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('table','index') ORDER BY rootpage"
    ).fetchall()
    for row in schema_rows:
        sql = row[0]
        if "sqlite_sequence" in sql:
            continue
        try:
            dst.execute(sql)
        except sqlite3.OperationalError:
            pass  # already exists (e.g. index on table just created)
    dst.commit()

    # Insert scraped_cafes — reset belongs_to_cafe_id and name_embedding for clean run
    cols = [c[1] for c in src.execute("PRAGMA table_info(scraped_cafes)").fetchall()]
    placeholders_cols = ",".join("?" * len(cols))
    col_list = ",".join(cols)

    reset_cols = {"belongs_to_cafe_id", "name_embedding", "llm_english"}

    def reset_row(row):
        return tuple(
            None if cols[i] in reset_cols else row[i]
            for i in range(len(cols))
        )

    dst.executemany(
        f"INSERT OR IGNORE INTO scraped_cafes ({col_list}) VALUES ({placeholders_cols})",
        [reset_row(r) for r in cafe_rows]
    )

    # Insert images
    if image_rows:
        img_cols = [c[1] for c in src.execute("PRAGMA table_info(images)").fetchall()]
        img_col_list = ",".join(img_cols)
        img_placeholders = ",".join("?" * len(img_cols))
        dst.executemany(
            f"INSERT OR IGNORE INTO images ({img_col_list}) VALUES ({img_placeholders})",
            [tuple(r) for r in image_rows]
        )

    dst.commit()

    # Verify
    n_cafes = dst.execute("SELECT COUNT(*) FROM scraped_cafes").fetchone()[0]
    n_images = dst.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    n_null = dst.execute("SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NULL").fetchone()[0]
    print()
    print(f"Written: {n_cafes} cafes ({n_null} unprocessed), {n_images} images → {args.target}")

    src.close()
    dst.close()


if __name__ == "__main__":
    main()
