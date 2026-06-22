#!/usr/bin/env python3
"""
clean_region.py — delete ONE region's rows from a DB, leaving other regions intact.

Built for the Busan iterate-and-check loop: wipe a Busan sample, refine the
algorithm, re-scrape — without ever touching Seoul. A region is defined by its
center + radius in data/regions.json; rows are selected by a geographic bounding
box around that center, so Seoul (≈270 km away) can never be matched.

SAFETY:
  * Dry-run by DEFAULT — prints exactly what WOULD be deleted, per DB and table.
  * Deletes only with --confirm, inside a single transaction per DB.
  * Never deletes shared/global data (cafe_chains, englishify.db).
  * Stop the region's scrapers + merge daemon before running with --confirm,
    so nothing writes the rows back mid-delete.

Usage:
    python data-processing/clean_region.py --region busan                # dry-run, both DBs
    python data-processing/clean_region.py --region busan --confirm       # execute
    python data-processing/clean_region.py --region busan --db data/seoul/clean.db
"""
import os
import sys
import math
import argparse
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper", "lib"))
import utils  # noqa: E402

DEFAULT_DBS = ["data/seoul/scraped.db", "data/seoul/clean.db"]


def region_bbox(region: str):
    """(lat_min, lat_max, lon_min, lon_max) covering the region's scrape radius."""
    lat, lon = utils.REGIONS[region]
    radius_km = utils.region_radius_km(region)
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)


def region_grid_band(region: str, bbox):
    """(gx_min, gx_max, gy_min, gy_max) — progress grid cells inside the bbox."""
    lat_min, lat_max, lon_min, lon_max = bbox
    gx_min = round((lon_min - utils.CENTER_LON) / utils.STEP_SIZE)
    gx_max = round((lon_max - utils.CENTER_LON) / utils.STEP_SIZE)
    gy_min = round((lat_min - utils.CENTER_LAT) / utils.STEP_SIZE)
    gy_max = round((lat_max - utils.CENTER_LAT) / utils.STEP_SIZE)
    return (gx_min, gx_max, gy_min, gy_max)


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def collect_targets(conn, bbox, band):
    """Return dict of {table: list_of_ids_or_None} that the region owns in this DB."""
    lat_min, lat_max, lon_min, lon_max = bbox
    gx_min, gx_max, gy_min, gy_max = band
    t = {}

    scraped_ids, clean_ids = [], []
    if table_exists(conn, "scraped_cafes"):
        scraped_ids = [r[0] for r in conn.execute(
            "SELECT id FROM scraped_cafes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat_min, lat_max, lon_min, lon_max))]
        t["scraped_cafes"] = scraped_ids
    if table_exists(conn, "clean_cafes"):
        clean_ids = [r[0] for r in conn.execute(
            "SELECT id FROM clean_cafes WHERE avg_lat BETWEEN ? AND ? AND avg_lon BETWEEN ? AND ?",
            (lat_min, lat_max, lon_min, lon_max))]
        t["clean_cafes"] = clean_ids

    image_ids = []
    if table_exists(conn, "images") and (scraped_ids or clean_ids):
        seen = set()
        # Images linked to a region cafe by either the raw scrape id (cafe_id)
        # or the merged clean id (belongs_to_cafe_id).
        for col, ids in (("cafe_id", scraped_ids), ("belongs_to_cafe_id", clean_ids)):
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                q = f"SELECT id FROM images WHERE {col} IN ({','.join('?' * len(chunk))})"
                for r in conn.execute(q, chunk):
                    if r[0] not in seen:
                        seen.add(r[0])
                        image_ids.append(r[0])
        t["images"] = image_ids

    if table_exists(conn, "image_tags") and image_ids:
        tagged = []
        for i in range(0, len(image_ids), 900):
            chunk = image_ids[i:i + 900]
            q = f"SELECT image_id FROM image_tags WHERE image_id IN ({','.join('?' * len(chunk))})"
            tagged += [r[0] for r in conn.execute(q, chunk)]
        t["image_tags"] = tagged

    if table_exists(conn, "progress"):
        prog = conn.execute(
            "SELECT COUNT(*) FROM progress WHERE grid_x BETWEEN ? AND ? AND grid_y BETWEEN ? AND ?",
            (gx_min, gx_max, gy_min, gy_max)).fetchone()[0]
        t["progress"] = prog  # count only (composite key, deleted by range)
    return t


def delete_targets(conn, bbox, band, targets):
    lat_min, lat_max, lon_min, lon_max = bbox
    gx_min, gx_max, gy_min, gy_max = band

    def del_by_ids(table, col, ids):
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            conn.execute(
                f"DELETE FROM {table} WHERE {col} IN ({','.join('?' * len(chunk))})", chunk)

    # Children first.
    if "image_tags" in targets:
        del_by_ids("image_tags", "image_id", targets["image_tags"])
    if "images" in targets:
        del_by_ids("images", "id", targets["images"])
    if "clean_cafes" in targets:
        del_by_ids("clean_cafes", "id", targets["clean_cafes"])
    if "scraped_cafes" in targets:
        del_by_ids("scraped_cafes", "id", targets["scraped_cafes"])
    if "progress" in targets:
        conn.execute(
            "DELETE FROM progress WHERE grid_x BETWEEN ? AND ? AND grid_y BETWEEN ? AND ?",
            (gx_min, gx_max, gy_min, gy_max))


def count_of(v):
    return v if isinstance(v, int) else len(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, help="Region name from regions.json (e.g. busan)")
    ap.add_argument("--db", nargs="*", default=DEFAULT_DBS, help="DB files to clean")
    ap.add_argument("--confirm", action="store_true", help="Actually delete (default: dry-run)")
    args = ap.parse_args()

    if args.region not in utils.REGIONS:
        sys.exit(f"Unknown region {args.region!r}; known: {sorted(utils.REGIONS)}")
    if args.region == "seoul":
        sys.exit("Refusing to clean 'seoul' — that is the protected production region.")

    bbox = region_bbox(args.region)
    band = region_grid_band(args.region, bbox)
    print(f"Region: {args.region}")
    print(f"  bbox  lat[{bbox[0]:.4f},{bbox[1]:.4f}] lon[{bbox[2]:.4f},{bbox[3]:.4f}]")
    print(f"  grid  x[{band[0]},{band[1]}] y[{band[2]},{band[3]}]")
    print(f"  mode  {'DELETE (--confirm)' if args.confirm else 'DRY-RUN'}\n")

    grand_total = 0
    for db in args.db:
        if not os.path.exists(db):
            print(f"  {db}: (missing, skipped)")
            continue
        conn = sqlite3.connect(db, timeout=60)
        conn.execute("PRAGMA busy_timeout=60000")
        targets = collect_targets(conn, bbox, band)
        total = sum(count_of(v) for v in targets.values())
        grand_total += total
        print(f"  {db}:")
        for tbl, v in targets.items():
            print(f"      {tbl:<14} {count_of(v):>8}")
        if total == 0:
            print("      (nothing in region)")
        if args.confirm and total:
            try:
                conn.execute("BEGIN")
                delete_targets(conn, bbox, band, targets)
                conn.commit()
                print(f"      → deleted {total} rows")
            except Exception as e:
                conn.rollback()
                print(f"      → FAILED, rolled back: {e}")
                conn.close()
                sys.exit(1)
        conn.close()

    if not args.confirm:
        print(f"\nDry-run only. {grand_total} rows match region '{args.region}'.")
        print("Re-run with --confirm to delete. (Stop region scrapers + merge daemon first.)")
    else:
        print(f"\nDone. Removed region '{args.region}' from {len(args.db)} DB(s).")


if __name__ == "__main__":
    main()
