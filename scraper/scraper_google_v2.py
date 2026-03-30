"""
scraper_google_v2.py
====================
IMPROVEMENTS OVER v1 (scraper_google.py):
  - Searches both English and Korean terms: "cafe", "카페", "커피전문점", "브런치카페"
    Each keyword hits a different set of places, significantly increasing yield.
  - Extracts proper Google Place ID: parses the `1s0x...` or `ChIJ...` ID from URL data param,
    uses that as provider_id instead of the raw data= string (which caused 50-char truncation).
  - Scrolls results pane 15× (was 5×) before extracting — loads more results.
  - Uses a smaller grid step: scraper still uses utils.STEP_SIZE but calls multiple keyword
    searches per grid cell.
  - Skips grids already completed only when ALL keywords have been scraped (tracked via
    a per-keyword progress key: google_{grid_x}_{grid_y}_{keyword}).

WHAT DID NOT WORK / LIMITATIONS:
  - Google Maps CAPTCHAs remain the main blocker at high request rates.
    Tor proxy helps but Google sometimes CAPTCHAs even over Tor.
  - Google Maps returns max ~20 results per search regardless of scrolling.
    This is a hard limit on the frontend; Places API would give better yield.
  - Image extraction from detail pages is unreliable — some places show no imgs in DOM.
"""

import os
import json
import time
import random
import argparse
import urllib.parse
import logging
import threading
import re
from playwright.sync_api import sync_playwright
import utils
from utils import init_db, get_spiral_coordinates, DATA_DIR, CENTER_LAT, CENTER_LON, normalize_provider_id, db_execute, flush_db_queue
from download_utils import download_image

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_google_v2.log"),
        logging.StreamHandler()
    ]
)

SEARCH_KEYWORDS = ["카페", "커피전문점", "cafe", "브런치카페"]

def watchdog(timeout):
    logging.error(f"Watchdog: hung for {timeout}s. Exiting.")
    os._exit(1)


def extract_place_id(url: str) -> str | None:
    """
    Extract a stable Google Place ID from a Maps URL.
    Tries: 1s<PlaceID> pattern inside the data= param, then falls back to a hash of the data param.
    """
    # Try to find the place ID like: !1s0x357ca... or !1sChIJ...
    m = re.search(r'!1s([^!&]+)', url)
    if m:
        pid = urllib.parse.unquote(m.group(1))
        # Normalize: keep only alphanum + : _ -
        return re.sub(r'[^a-zA-Z0-9:_\-]', '_', pid)[:120]
    # Fallback: use data= param
    m2 = re.search(r'data=([^&]+)', url)
    if m2:
        raw = m2.group(1)
        return re.sub(r'[^a-zA-Z0-9]+', '_', raw)[:120]
    return None


def dismiss_consent(page):
    try:
        page.wait_for_timeout(2000)
        for text in ["Accept all", "Reject all", "Alle akzeptieren", "Alle ablehnen",
                     "Tout accepter", "Tout refuser"]:
            try:
                btn = page.locator(f"button:has-text('{text}')")
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    return
            except Exception:
                pass
        if "consent.google.com" in page.url:
            buttons = page.locator("form").first.locator("button")
            if buttons.count() >= 2:
                buttons.nth(1).click(timeout=3000)
                page.wait_for_timeout(2000)
    except Exception:
        pass


def scrape_keyword(page, conn, grid_x, grid_y, lat, lon, keyword):
    provider = 'google'
    progress_key = f"{grid_x}_{grid_y}_{keyword}"

    cursor = conn.cursor()
    cursor.execute('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?',
                   (grid_x, grid_y, f"{provider}_{keyword}"))
    row = cursor.fetchone()
    if row and row[0] == 'completed':
        return True

    encoded = urllib.parse.quote(keyword)
    url = f"https://www.google.com/maps/search/{encoded}/@{lat},{lon},15z?hl=en"

    try:
        page.set_default_timeout(60000)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        dismiss_consent(page)

        results_selector = 'div[role="feed"]'
        try:
            page.wait_for_selector(results_selector, timeout=12000)
        except Exception:
            logging.warning(f"No results pane for ({grid_x},{grid_y}) keyword={keyword}")
            page.screenshot(path=f"log/error_{grid_x}_{grid_y}_{keyword}.png")
            return False

        # Scroll 15× to load more results
        for _ in range(15):
            page.evaluate(f'''
                const pane = document.querySelector('{results_selector}');
                if (pane) pane.scrollTop = pane.scrollHeight;
            ''')
            page.wait_for_timeout(800)

        places = page.evaluate(r'''() => {
            const results = [];
            const items = document.querySelectorAll('a[href^="https://www.google.com/maps/place/"]');
            items.forEach(item => {
                const url = item.href;
                const ariaLabel = item.getAttribute('aria-label') || '';
                let lat = null, lon = null;
                const coordMatch = url.match(/!3d([\d.\-]+)!4d([\d.\-]+)/);
                if (coordMatch) {
                    lat = parseFloat(coordMatch[1]);
                    lon = parseFloat(coordMatch[2]);
                }
                if (url && ariaLabel) {
                    results.push({ url, name: ariaLabel, lat, lon });
                }
            });
            const seen = new Set();
            return results.filter(r => {
                if (seen.has(r.url)) return false;
                seen.add(r.url);
                return true;
            });
        }''')

        logging.info(f"  ({grid_x},{grid_y}) [{keyword}]: {len(places)} places")

        for place in places:
            place_id = extract_place_id(place['url'])
            if not place_id:
                continue

            name = place['name']
            p_lat = place['lat'] or lat
            p_lon = place['lon'] or lon
            global_id = f"{provider}_{place_id}"
            safe_id = normalize_provider_id(place_id)
            cafe_dir = os.path.join(DATA_DIR, provider, safe_id)
            images_dir = os.path.join(cafe_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)

            # Quick image grab from detail page
            local_images = []
            try:
                dp = page.context.new_page()
                dp.goto(place['url'], wait_until="domcontentloaded", timeout=20000)
                dp.wait_for_timeout(2000)
                imgs = dp.evaluate(r'''() => {
                    return Array.from(document.querySelectorAll('img'))
                        .map(i => i.src)
                        .filter(s => s && s.includes('googleusercontent.com') && !s.includes('Avatar'))
                        .map(s => s.replace(/=w\d+-h\d+/, '=w1024-h768'))
                        .filter((v, i, a) => a.indexOf(v) === i)
                        .slice(0, 10);
                }''')
                dp.close()
                for i, img_url in enumerate(imgs):
                    ext = img_url.split('?')[0].split('.')[-1]
                    if ext.lower() not in ('jpg','jpeg','png','gif','webp'):
                        ext = 'jpg'
                    fname = f"img_{i}.{ext}"
                    fp = os.path.join(images_dir, fname)
                    if download_image(img_url, fp):
                        local_images.append(f"/images/{provider}/{safe_id}/images/{fname}")
            except Exception as e:
                logging.debug(f"  Detail page error for {name}: {e}")
                try:
                    dp.close()
                except Exception:
                    pass

            meta = {'name': name, 'lat': p_lat, 'lon': p_lon, 'url': place['url'],
                    'local_images': local_images, 'keyword': keyword}

            db_execute(conn, '''
                INSERT OR REPLACE INTO cafes (id, provider, provider_id, name, lat, lon, address, url, metadata)
                VALUES (?,?,?,?,?,?,?,?,?)
            ''', (global_id, provider, place_id, name, p_lat, p_lon, '', place['url'],
                  json.dumps(meta, ensure_ascii=False)))

            with open(os.path.join(cafe_dir, 'cafe.json'), 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

        db_execute(conn, '''
            INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
            VALUES (?,?,?,?)
        ''', (grid_x, grid_y, f"{provider}_{keyword}", 'completed'))

        time.sleep(random.uniform(1.5, 3.0))
        return True

    except Exception as e:
        logging.error(f"Error ({grid_x},{grid_y}) [{keyword}]: {e}")
        return False


def scrape_google_grid(page, conn, grid_x, grid_y, lat, lon):
    results = []
    for keyword in SEARCH_KEYWORDS:
        timer = threading.Timer(300, watchdog, args=[300])
        timer.start()
        try:
            ok = scrape_keyword(page, conn, grid_x, grid_y, lat, lon, keyword)
            results.append(ok)
        finally:
            timer.cancel()
        time.sleep(random.uniform(1.0, 2.0))
    return all(results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--start-step", type=int, default=0)
    args = parser.parse_args()

    conn = init_db()
    utils._current_provider = 'google'
    coords = get_spiral_coordinates(args.max_steps)
    coords_to_process = coords[args.start_step:]

    logging.info(f"Processing {len(coords_to_process)} grids, {len(SEARCH_KEYWORDS)} keywords each")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"}
            )
        except Exception:
            browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )
        page = context.new_page()

        for idx, (x, y) in enumerate(coords_to_process):
            logging.info(f"--- Step {args.start_step + idx}/{args.max_steps} ({x},{y}) ---")
            grid_lat = CENTER_LAT + (y * 0.01)
            grid_lon = CENTER_LON + (x * 0.01)

            for attempt in range(2):
                try:
                    ok = scrape_google_grid(page, conn, x, y, grid_lat, grid_lon)
                    if ok:
                        break
                except Exception as e:
                    logging.error(f"Grid error attempt {attempt}: {e}")
                    time.sleep(5)

        browser.close()

    flush_db_queue(conn)
    conn.close()
    logging.info("Scraping iteration complete.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
