"""
scraper_kakao_v2.py
===================
IMPROVEMENTS OVER v1 (scraper_kakao.py):
  - Proper API pagination via the searchJson endpoint intercepted from network traffic.
    Iterates pages until 'hasNext' is false instead of clicking "더보기" 3 times.
  - v2 original: searched multiple keywords per grid: 카페, 커피, 브런치, 디저트카페
  - v2 updated: single keyword "카페" + category filter (CE7-equivalent).
    Extracts txt_ginfo category text from HTML and skips non-cafe categories.
    Progress key changed to "kakao_CE7" so previously scraped grids are re-scraped
    with the cleaner category filter.
  - Coordinate conversion uses same pyproj transform from v1.
  - Image downloading removed from this scraper — handled by scraper_kakao_images_v3.py.
    This keeps the data scraper fast and focused.

WHAT DID NOT WORK / LIMITATIONS:
  - The searchJson XHR endpoint appears to no longer fire on mobile web; results are
    now server-rendered HTML. Scraper falls back to HTML parsing (data-id/wx/wy/title)
    plus txt_ginfo for category.
  - Dense areas may still miss some places due to page result limits.
  - No Tor for Kakao — the mobile API doesn't seem to rate-limit aggressively.
    Add Tor if you start seeing 429s.

CATEGORY FILTER (CE7):
  Kakao CE7 = 카페 group. Skips places whose txt_ginfo matches NON_CAFE_CATEGORIES.
  Unknown/empty category is kept (keyword match "카페" implies relevance).
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

SEARCH_QUERY = "카페"
PROGRESS_KEY = "kakao_CE7"

# Place categories that are clearly not cafes — skip these.
# Everything else (including blank) is kept; "카페" keyword match implies relevance.
NON_CAFE_CATEGORIES = {
    "편의점", "마트", "대형마트", "슈퍼마켓", "음식점", "한식", "중식", "일식",
    "양식", "분식", "치킨", "피자", "햄버거", "패스트푸드", "술집", "호프",
    "노래방", "PC방", "게임", "약국", "병원", "미용실", "헤어샵", "네일",
    "은행", "주유소", "세탁소", "헬스장", "피트니스", "스포츠",
}

_shutdown = threading.Event()


def _sigterm(sig, frame):
    print("SIGTERM received — finishing current grid then exiting.", flush=True)
    _shutdown.set()


signal.signal(signal.SIGTERM, _sigterm)


def watchdog(timeout):
    print(f"Watchdog: hung for {timeout}s. Exiting.")
    os._exit(1)


def scrape_grid(browser, dbc, grid_x, grid_y, lat, lon):
    provider = 'kakao'

    row = dbc.fetchone('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?',
                       (grid_x, grid_y, PROGRESS_KEY))
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
    encoded_kw = urllib.parse.quote(SEARCH_QUERY)
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
        # Extract id, coords, title, and category (txt_ginfo span) from each result item
        item_pattern = re.compile(
            r'<li[^>]*data-id="(\d+)"[^>]*data-wx="(\d+)"[^>]*data-wy="(\d+)"[^>]*data-title="([^"]+)"[^>]*>.*?'
            r'(?:<span[^>]*class="[^"]*txt_ginfo[^"]*"[^>]*>(.*?)</span>)?',
            re.DOTALL
        )
        for m in item_pattern.finditer(html):
            pid, pwx, pwy, ptitle, pcategory = m.group(1), m.group(2), m.group(3), m.group(4), (m.group(5) or '').strip()
            if pid not in unique:
                unique[pid] = {'confirmid': pid, 'name': ptitle, 'x': int(pwx), 'y': int(pwy), 'category': pcategory}

        # Apply CE7 category filter: skip places in NON_CAFE_CATEGORIES
        filtered = {pid: p for pid, p in unique.items() if p.get('category', '') not in NON_CAFE_CATEGORIES}
        skipped = len(unique) - len(filtered)
        if skipped:
            print(f"  ({grid_x},{grid_y}): skipped {skipped} non-cafe places (CE7 filter)")
        unique = filtered

        print(f"  ({grid_x},{grid_y}) [CE7]: {len(unique)} places from {len(api_pages)} searchJson + HTML")

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
        ''', (grid_x, grid_y, PROGRESS_KEY, 'completed'))

        time.sleep(random.uniform(1.5, 3.0))
        page.close()
        context.close()
        return True

    except Exception as e:
        print(f"  Error ({grid_x},{grid_y}) [CE7]: {e}")
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

    if args.max_steps >= 2000 and check_if_done(dbc, [PROGRESS_KEY], coords):
        print("All blocks within 20km radius are completed! Shutting down.")
        dbc.execute("INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status) VALUES (?, ?, ?, ?)",
                    (9999, 9999, 'kakao_finished', 'completed'))
        import sys; sys.exit(42)

    coords_to_process = coords[args.start_step:]
    pass # print(f"Processing {len(coords_to_process)} grids (CE7 category filter)")

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
            pass # print(f"--- Step {step}/{args.max_steps} ({gx},{gy}) ---")
            grid_lat = CENTER_LAT + (gy * 0.01)
            grid_lon = CENTER_LON + (gx * 0.01)

            timer = threading.Timer(300, watchdog, args=[300])
            timer.start()
            try:
                ok = scrape_grid(browser, dbc, gx, gy, grid_lat, grid_lon)
                if not ok:
                    time.sleep(3)
            finally:
                timer.cancel()

        browser.close()

    dbc.close()
    pass # print("Scraping iteration complete.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
