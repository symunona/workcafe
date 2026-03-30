"""
scraper_google_v3.py — slow, patient Google Maps scraper
=========================================================
Strategy to avoid reCAPTCHA:
  - Clearnet only (no Tor — Tor exit IPs are permanently flagged by Google)
  - Fresh browser context per search (new fingerprint each time)
  - ONE keyword per grid cell per run; keywords rotate across ralph_loop cycles
  - 60–90s sleep between every search request (~1/min throughput)
  - Pre-warms each session with a google.com visit before going to maps
  - Randomised UA, viewport, locale, accept-language from realistic pools
  - CAPTCHA detection: on consecutive failures, backs off 5–15 min
  - No detail-page image scraping (keeps request count minimal)

At ~1 req/min this scrapes slowly but consistently without getting blocked.
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
from utils import (
    init_db, get_spiral_coordinates, DATA_DIR, CENTER_LAT, CENTER_LON,
    normalize_provider_id, db_execute, flush_db_queue,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_google_v3.log"),
        logging.StreamHandler()
    ]
)

SEARCH_KEYWORDS = ["카페", "커피전문점", "cafe", "브런치카페"]

# Realistic Chrome UAs across OS/version combinations
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
    {"width": 2560, "height": 1440},
]

LOCALES = ["en-US", "en-GB", "en-KR"]


def watchdog(timeout):
    logging.error(f"Watchdog: hung for {timeout}s. Exiting.")
    os._exit(1)


def extract_place_id(url: str) -> str | None:
    m = re.search(r'!1s([^!&]+)', url)
    if m:
        pid = urllib.parse.unquote(m.group(1))
        return re.sub(r'[^a-zA-Z0-9:_\-]', '_', pid)[:120]
    m2 = re.search(r'data=([^&]+)', url)
    if m2:
        return re.sub(r'[^a-zA-Z0-9]+', '_', m2.group(1))[:120]
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
                    page.wait_for_timeout(1500)
                    return
            except Exception:
                pass
        if "consent.google.com" in page.url:
            buttons = page.locator("form").first.locator("button")
            if buttons.count() >= 2:
                buttons.nth(1).click(timeout=3000)
                page.wait_for_timeout(1500)
    except Exception:
        pass


def is_captcha_page(page) -> bool:
    """Return True if the current page is a CAPTCHA / unusual-traffic page."""
    try:
        content = page.content()
        return (
            "recaptcha" in content.lower()
            or "unusual traffic" in content.lower()
            or "not a robot" in content.lower()
            or "consent.google.com" in page.url
        )
    except Exception:
        return False


def warmup(page):
    """Visit google.com briefly before going to Maps — looks more human."""
    try:
        page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(random.uniform(2000, 4000))
        dismiss_consent(page)
        page.wait_for_timeout(random.uniform(1000, 2000))
    except Exception:
        pass


def scrape_one(playwright_instance, conn, grid_x, grid_y, lat, lon, keyword) -> bool:
    """
    Open a fresh browser context, search once, extract results, close context.
    Returns True on success (even if 0 results), False on CAPTCHA/error.
    """
    provider = 'google'

    cursor = conn.cursor()
    cursor.execute('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?',
                   (grid_x, grid_y, f"{provider}_{keyword}"))
    row = cursor.fetchone()
    if row and row[0] == 'completed':
        return True  # already done

    ua       = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)
    locale   = random.choice(LOCALES)

    browser = playwright_instance.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale=locale,
        java_script_enabled=True,
    )
    page = context.new_page()

    try:
        warmup(page)

        encoded = urllib.parse.quote(keyword)
        url = f"https://www.google.com/maps/search/{encoded}/@{lat},{lon},15z?hl=en"

        page.set_default_timeout(60000)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(random.uniform(3000, 5000))

        dismiss_consent(page)

        if is_captcha_page(page):
            logging.warning(f"  ({grid_x},{grid_y}) [{keyword}]: CAPTCHA detected")
            page.screenshot(path=f"log/captcha_{grid_x}_{grid_y}_{keyword}.png")
            return False

        results_selector = 'div[role="feed"]'
        try:
            page.wait_for_selector(results_selector, timeout=15000)
        except Exception:
            if is_captcha_page(page):
                logging.warning(f"  ({grid_x},{grid_y}) [{keyword}]: CAPTCHA (no feed)")
                page.screenshot(path=f"log/captcha_{grid_x}_{grid_y}_{keyword}.png")
                return False
            logging.warning(f"  ({grid_x},{grid_y}) [{keyword}]: no results pane")
            # Still mark completed to avoid retrying zero-result areas forever
            db_execute(conn, '''INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
                                VALUES (?,?,?,?)''',
                       (grid_x, grid_y, f"{provider}_{keyword}", 'completed'))
            return True

        # Scroll gently — fewer scrolls, longer waits
        for _ in range(8):
            page.evaluate(f'''
                const pane = document.querySelector('{results_selector}');
                if (pane) pane.scrollTop = pane.scrollHeight;
            ''')
            page.wait_for_timeout(random.uniform(600, 1200))

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
                if (url && ariaLabel) results.push({ url, name: ariaLabel, lat, lon });
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
            os.makedirs(os.path.join(cafe_dir, 'images'), exist_ok=True)

            meta = {'name': name, 'lat': p_lat, 'lon': p_lon,
                    'url': place['url'], 'keyword': keyword}

            db_execute(conn, '''
                INSERT OR REPLACE INTO cafes (id, provider, provider_id, name, lat, lon, address, url, metadata)
                VALUES (?,?,?,?,?,?,?,?,?)
            ''', (global_id, provider, place_id, name, p_lat, p_lon, '', place['url'],
                  json.dumps(meta, ensure_ascii=False)))

            with open(os.path.join(cafe_dir, 'cafe.json'), 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

        db_execute(conn, '''INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
                            VALUES (?,?,?,?)''',
                   (grid_x, grid_y, f"{provider}_{keyword}", 'completed'))
        return True

    except Exception as e:
        logging.error(f"  ({grid_x},{grid_y}) [{keyword}]: {e}")
        return False
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--start-step", type=int, default=0)
    args = parser.parse_args()

    conn = init_db()
    utils._current_provider = 'google'
    coords = get_spiral_coordinates(args.max_steps)
    coords_to_process = coords[args.start_step:]

    # Flatten grid × keyword into a single work list, skip already-done combos
    work = []
    for x, y in coords_to_process:
        for kw in SEARCH_KEYWORDS:
            work.append((x, y, kw))

    logging.info(f"Google v3 (slow mode): {len(work)} grid×keyword combos to attempt")

    consecutive_captchas = 0

    with sync_playwright() as p:
        for x, y, keyword in work:
            grid_lat = CENTER_LAT + (y * 0.01)
            grid_lon = CENTER_LON + (x * 0.01)

            logging.info(f"--- ({x},{y}) [{keyword}] ---")

            timer = threading.Timer(300, watchdog, args=[300])
            timer.start()
            try:
                ok = scrape_one(p, conn, x, y, grid_lat, grid_lon, keyword)
            finally:
                timer.cancel()

            if ok:
                consecutive_captchas = 0
                # Human-pace pause between successful searches
                sleep_s = random.uniform(60, 90)
                logging.info(f"  sleeping {sleep_s:.0f}s before next search")
                time.sleep(sleep_s)
            else:
                consecutive_captchas += 1
                if consecutive_captchas >= 3:
                    # Sustained blocking — long cooldown
                    cooldown = random.uniform(600, 900)
                    logging.warning(f"  {consecutive_captchas} consecutive CAPTCHAs — cooling down {cooldown:.0f}s")
                    time.sleep(cooldown)
                    consecutive_captchas = 0
                else:
                    # Single failure — shorter pause then try next grid
                    sleep_s = random.uniform(120, 180)
                    logging.info(f"  CAPTCHA #{consecutive_captchas}, sleeping {sleep_s:.0f}s")
                    time.sleep(sleep_s)

    flush_db_queue(conn)
    conn.close()
    logging.info("Scraping iteration complete.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
