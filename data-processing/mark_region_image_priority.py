#!/usr/bin/env python3
"""
mark_region_image_priority.py — front-load image scraping for one region.

Sets kakao_scrape_state.priority=1 on every kakao cafe inside a region's
bounding box (same bbox definition as clean_region.py / regions.json). The
kakao image scraper's queue picker serves priority>0 cafes first and breadth-
first caps each at ~image_priority_first_n images, so marking a region
front-loads ~30 photos/cafe across it before the deep-scrape backlog.

Additive + idempotent: only flips priority 0→1; never touches scraped fields.

Writes through the db_server socket (default scraped.db) so it is safe to run
while the image scraper is live. Region bbox is computed from data/regions.json.

Usage:
    python data-processing/mark_region_image_priority.py --region busan
    python data-processing/mark_region_image_priority.py --region busan --dry-run
"""
import os
import sys
import math
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))

import utils  # noqa: E402
from db_client import DBClient  # noqa: E402


def region_bbox(region: str):
    """(lat_min, lat_max, lon_min, lon_max) covering the region's scrape radius.
    Mirrors clean_region.region_bbox so the same rows are selected."""
    if region not in utils.REGIONS:
        sys.exit(f"ERROR: unknown region '{region}'. Known: {sorted(utils.REGIONS)}")
    lat, lon = utils.REGIONS[region]
    radius_km = utils.region_radius_km(region)
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--socket", default=utils.DB_SOCKET_PATH,
                    help="db_server socket (default scraped.db socket)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print how many rows would be prioritized, write nothing")
    args = ap.parse_args()

    lat_min, lat_max, lon_min, lon_max = region_bbox(args.region)
    dbc = DBClient(socket_path=args.socket)

    # Kakao cafes inside the bbox that still have pending image scrape state.
    n_target = dbc.fetchval(
        """SELECT COUNT(*)
             FROM kakao_scrape_state s
             JOIN scraped_cafes c ON c.id = s.cafe_id
            WHERE c.provider = 'kakao' AND s.status = 'pending'
              AND c.lat BETWEEN ? AND ? AND c.lon BETWEEN ? AND ?""",
        (lat_min, lat_max, lon_min, lon_max),
    ) or 0

    print(f"region={args.region} bbox=({lat_min:.4f},{lat_max:.4f},"
          f"{lon_min:.4f},{lon_max:.4f})")
    print(f"pending kakao cafes in bbox: {n_target}")

    if args.dry_run:
        print("[dry-run] no writes.")
        return

    resp = dbc.execute(
        """UPDATE kakao_scrape_state
              SET priority = 1
            WHERE cafe_id IN (
                SELECT s.cafe_id
                  FROM kakao_scrape_state s
                  JOIN scraped_cafes c ON c.id = s.cafe_id
                 WHERE c.provider = 'kakao' AND s.status = 'pending'
                   AND c.lat BETWEEN ? AND ? AND c.lon BETWEEN ? AND ?)
              AND COALESCE(priority, 0) = 0""",
        (lat_min, lat_max, lon_min, lon_max),
    )
    print(f"priority=1 set on {resp.get('rowcount', 0)} kakao_scrape_state rows.")


if __name__ == "__main__":
    main()
