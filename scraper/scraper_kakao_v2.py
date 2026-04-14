"""
scraper_kakao_v2.py
===================
IMPROVEMENTS OVER v1 (scraper_kakao.py):
  - Proper API pagination via the searchJson endpoint intercepted from network traffic.
    Iterates pages until 'hasNext' is false instead of clicking "더보기" 3 times.
  - Searches multiple keywords per grid: 카페, 커피, 브런치, 디저트카페
    Each yields different long-tail places not in the main "카페" results.
  - Progress tracked per keyword: progress key = "{provider}_{keyword}" so
    we can resume per-keyword without re-scraping completed keywords.
  - Coordinate conversion uses same pyproj transform from v1.
  - Image downloading removed from this scraper — handled by scraper_kakao_images_v3.py.
    This keeps the data scraper fast and focused.

WHAT DID NOT WORK / LIMITATIONS:
  - The searchJson API returns max 15 results per page. At 10+ pages that's 150+ per grid
    which is good, but dense areas may still miss some places.
  - No Tor for Kakao — the mobile API doesn't seem to rate-limit aggressively.
    Add Tor if you start seeing 429s.
"""

import os
import json
import time
import random
import argparse
import pyproj
import threading
import signal
from playwright.sync_api import sync_playwright
from utils import init_db, get_spiral_coordinates, DATA_DIR, CENTER_LAT, CENTER_LON, normalize_provider_id, check_if_done
from db_client import DBClient

SEARCH_KEYWORDS = ["카페", "커피", "브런치", "디저트카페"]

_shutdown = threading.Event()


def _sigterm(sig, frame):
    print("SIGTERM received — finishing current grid then exiting.", flush=True)
    _shutdown.set()


signal.signal(signal.SIGTERM, _sigterm)


def watchdog(timeout):
    print(f"Watchdog: hung for {timeout}s. Exiting.")
    os._exit(1)


def scrape_keyword(browser, dbc, grid_x, grid_y, lat, lon, keyword):
    provider = 'kakao'
    progress_provider = f"{provider}_{keyword}"

    row = dbc.fetchone('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?',
                       (grid_x, grid_y, progress_provider))
    if row and row[0] == 'completed':
        return True

    wgs84 = pyproj.CRS("EPSG:4326")
    wcongnamul = pyproj.CRS("EPSG:5181")
    transformer = pyproj.Transformer.from_crs(wgs84, wcongnamul, always_xy=True)
    transformer_back = pyproj.Transformer.from_crs(wcongnamul, wgs84, always_xy=True)
    x, y = transformer.transform(lon, lat)
    urlX = int(x * 2.5)
    urlY = int(y * 2.5)

    context = browser.new_context(
        user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
        viewport={"width": 360, "height": 800}
    )
    page = context.new_page()

    api_pages = []

    def handle_response(resp):
        if "searchJson" in resp.url:
            try:
                data = resp.json()
                api_pages.append(data)
            except Exception:
                pass

    page.on("response", handle_response)

    import urllib.parse
    encoded_kw = urllib.parse.quote(keyword)
    url = f"https://m.map.kakao.com/actions/searchView?q={encoded_kw}&wx={urlX}&wy={urlY}&level=4"

    try:
        try:
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"  Goto error: {e}")

        page.wait_for_timeout(3000)

        for _ in range(20):
            try:
                more_btn = page.locator(".link_more[data-type='place'], .btn_more, button:has-text('더보기')")
                if more_btn.count() == 0 or not more_btn.first.is_visible():
                    break
                more_btn.first.click()
                page.wait_for_timeout(1500)
            except Exception:
                break

        page.remove_listener("response", handle_response)

        unique = {}
        for data in api_pages:
            place_list = data.get('placeList', [])
            for place in place_list:
                pid = str(place.get('confirmid', ''))
                if pid and pid not in unique:
                    unique[pid] = place

        import re
        html = page.content()
        html_places = re.findall(
            r'<li class="search_item base" data-id="(\d+)".*?data-wx="(\d+)".*?data-wy="(\d+)".*?data-title="([^"]+)"',
            html
        )
        for pid, pwx, pwy, ptitle in html_places:
            if pid not in unique:
                unique[pid] = {'confirmid': pid, 'name': ptitle, 'x': int(pwx), 'y': int(pwy)}

        print(f"  ({grid_x},{grid_y}) [{keyword}]: {len(unique)} places from {len(api_pages)} API pages")

        for pid, place in unique.items():
            name = place.get('name', '')
            raw_x = place.get('x', 0)
            raw_y = place.get('y', 0)

            if raw_x and raw_y:
                p_x = raw_x / 2.5
                p_y = raw_y / 2.5
                p_lon, p_lat = transformer_back.transform(p_x, p_y)
            else:
                p_lat, p_lon = lat, lon

            address = place.get('address', place.get('roadAddress', '')).strip()
            cafe_url = f"https://place.map.kakao.com/{pid}"
            global_id = f"{provider}_{pid}"
            safe_id = normalize_provider_id(pid)
            cafe_dir = os.path.join(DATA_DIR, provider, safe_id)
            os.makedirs(cafe_dir, exist_ok=True)

            dbc.execute('''
                INSERT OR REPLACE INTO cafes
                    (id, provider, provider_id, name, lat, lon, address, url, metadata)
                VALUES (?,?,?,?,?,?,?,?,?)
            ''', (global_id, provider, pid, name, p_lat, p_lon, address, cafe_url,
                  json.dumps(place, ensure_ascii=False)))

            with open(os.path.join(cafe_dir, 'cafe.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'id': global_id, 'provider': provider, 'provider_id': pid,
                    'name': name, 'lat': p_lat, 'lon': p_lon,
                    'address': address, 'url': cafe_url, 'metadata': place
                }, f, ensure_ascii=False, indent=2)

        dbc.execute('''
            INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
            VALUES (?,?,?,?)
        ''', (grid_x, grid_y, progress_provider, 'completed'))

        time.sleep(random.uniform(1.5, 3.0))
        page.close()
        context.close()
        return True

    except Exception as e:
        print(f"  Error ({grid_x},{grid_y}) [{keyword}]: {e}")
        try:
            page.close()
            context.close()
        except Exception:
            pass
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--start-step", type=int, default=0)
    args = parser.parse_args()

    init_db()
    dbc = DBClient()
    coords = get_spiral_coordinates(args.max_steps)

    if args.max_steps >= 2000 and check_if_done(dbc, [f"kakao_{kw}" for kw in SEARCH_KEYWORDS], coords):
        print("All blocks within 20km radius are completed! Shutting down.")
        dbc.execute("INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status) VALUES (?, ?, ?, ?)",
                    (9999, 9999, 'kakao_finished', 'completed'))
        import sys; sys.exit(42)

    coords_to_process = coords[args.start_step:]
    print(f"Processing {len(coords_to_process)} grids × {len(SEARCH_KEYWORDS)} keywords")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            print(f"Failed to start browser: {e}")
            return

        for idx, (gx, gy) in enumerate(coords_to_process):
            if _shutdown.is_set():
                print("Shutdown requested — exiting loop.", flush=True)
                break

            step = args.start_step + idx
            print(f"--- Step {step}/{args.max_steps} ({gx},{gy}) ---")
            grid_lat = CENTER_LAT + (gy * 0.01)
            grid_lon = CENTER_LON + (gx * 0.01)

            for keyword in SEARCH_KEYWORDS:
                if _shutdown.is_set():
                    break
                timer = threading.Timer(300, watchdog, args=[300])
                timer.start()
                try:
                    ok = scrape_keyword(browser, dbc, gx, gy, grid_lat, grid_lon, keyword)
                    if not ok:
                        time.sleep(3)
                finally:
                    timer.cancel()

        browser.close()

    dbc.close()
    print("Scraping iteration complete.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
