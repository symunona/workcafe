"""
scraper_google_images_v1.py
===========================

Standalone image scraper for Google Maps cafe entries.

Strategy:
  - Reads existing Google cafes from the DB (those without entries in the
    `images` table come first).
  - For each cafe, opens its stored Google Maps URL in a fresh Playwright
    browser context, dismisses the consent popup, and extracts all
    googleusercontent.com images from the rendered DOM.
  - Writes downloaded images to data/seoul/google/{safe_id}/images/ and
    inserts rows into the `images` table (same schema as Kakao v3).
  - Sleeps 60–90s between cafes to avoid reCAPTCHA.
  - On CAPTCHA detection backs off 5–15 min.

Usage:
    cd scraper && source ../venv/bin/activate
    python scraper_google_images_v1.py [--limit N] [--cafe-id google_XXXXX] [--force]
"""

import os
import sys
import json
import time
import random
import logging
import argparse
import signal
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

import requests
from playwright.sync_api import sync_playwright

from utils import DATA_DIR, normalize_provider_id
from db_client import DBClient
from disk_check import check_disk_limit, DiskLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_google_images_v1.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
SLEEP_BETWEEN_CAFES = (60, 90)
CAPTCHA_SLEEP = (300, 900)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

_shutdown = threading.Event()


def _sigterm(sig, frame):
    log.info("SIGTERM received — finishing current cafe then exiting.")
    _shutdown.set()


signal.signal(signal.SIGTERM, _sigterm)


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


def is_captcha(page) -> bool:
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
    try:
        page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(random.uniform(2000, 3000))
        dismiss_consent(page)
    except Exception:
        pass


def download_image(session, url, save_path):
    try:
        r = session.get(url, stream=True, timeout=15)
        r.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        log.debug(f"  Download failed {url[:60]}: {e}")
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
        return False


def process_cafe(dbc, page, session, cafe_id, provider_id, cafe_url, force=False):
    safe_id = normalize_provider_id(provider_id)
    img_dir = os.path.join(DATA_DIR, 'google', safe_id, 'images')

    db_count = dbc.fetchval('SELECT COUNT(*) FROM images WHERE cafe_id=?', (cafe_id,))
    if db_count > 0 and not force:
        log.info(f"  Skip {cafe_id}: {db_count} images already in DB")
        return db_count

    if not cafe_url:
        log.warning(f"  {cafe_id}: no URL stored, skipping")
        return 0

    log.info(f"Processing {cafe_id}")

    try:
        page.goto(cafe_url, wait_until="domcontentloaded", timeout=25000)
    except Exception as e:
        log.warning(f"  {cafe_id}: navigation error: {e}")
        return 0

    dismiss_consent(page)
    page.wait_for_timeout(3000)

    if is_captcha(page):
        sleep_s = random.uniform(*CAPTCHA_SLEEP)
        log.warning(f"  CAPTCHA detected, sleeping {sleep_s:.0f}s")
        time.sleep(sleep_s)
        return 0

    imgs = page.evaluate("""() =>
        Array.from(document.querySelectorAll('img'))
            .map(i => i.src)
            .filter(s => s && s.includes('googleusercontent.com') && !s.includes('Avatar'))
            .map(s => s.replace(/=w\\d+-h\\d+/, '=w800-h600'))
            .filter((v, i, a) => a.indexOf(v) === i)
            .slice(0, 20)
    """)

    if not imgs:
        log.info(f"  {cafe_id}: no images found on page")
        return 0

    log.info(f"  {cafe_id}: {len(imgs)} images found")
    os.makedirs(img_dir, exist_ok=True)

    downloaded = 0
    for idx, img_url in enumerate(imgs):
        if _shutdown.is_set():
            break

        ext = img_url.split('?')[0].split('.')[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
            ext = 'jpg'
        fname = f"img_{idx}.{ext}"
        save_path = os.path.join(img_dir, fname)

        if os.path.exists(save_path) and not force:
            downloaded += 1
            continue

        try:
            check_disk_limit()
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break

        if download_image(session, img_url, save_path):
            downloaded += 1
            local_path = f"/images/google/{safe_id}/images/{fname}"
            photo_id = f"{cafe_id}_{idx}"
            row_exists = dbc.fetchone(
                'SELECT 1 FROM images WHERE cafe_id=? AND photo_id=?',
                (cafe_id, photo_id)
            )
            if not row_exists:
                dbc.execute('''
                    INSERT OR REPLACE INTO images
                      (cafe_id, provider, local_path, image_url, photo_id)
                    VALUES (?,?,?,?,?)
                ''', (cafe_id, 'google', local_path, img_url, photo_id))

    log.info(f"  {cafe_id}: {downloaded}/{len(imgs)} downloaded")
    return downloaded


def run(args):
    dbc = DBClient()

    if args.cafe_id:
        rows = dbc.fetchall(
            'SELECT id, provider_id, url FROM cafes WHERE id=? AND provider=?',
            (args.cafe_id, 'google')
        )
    else:
        rows = dbc.fetchall('''
            SELECT c.id, c.provider_id, c.url FROM cafes c
            WHERE c.provider = 'google'
            ORDER BY
                (SELECT COUNT(*) FROM images i WHERE i.cafe_id = c.id) ASC,
                c.id ASC
        ''')

    if args.limit > 0:
        rows = rows[:args.limit]

    log.info(f"Processing {len(rows)} Google cafes")

    session = requests.Session()
    session.headers.update({
        'User-Agent': USER_AGENTS[0],
        'Referer': 'https://www.google.com/',
    })

    total = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale='en-US',
        )
        page = ctx.new_page()
        warmup(page)

        for i, (cafe_id, provider_id, cafe_url) in enumerate(rows):
            if _shutdown.is_set():
                log.info("Shutdown requested — exiting loop.")
                break

            n = process_cafe(dbc, page, session, cafe_id, provider_id, cafe_url,
                             force=args.force)
            total += n

            if (i + 1) % 10 == 0:
                log.info(f"Progress: {i+1}/{len(rows)} cafes, {total} images total")

            if i < len(rows) - 1 and not _shutdown.is_set():
                sleep_s = random.uniform(*SLEEP_BETWEEN_CAFES)
                log.info(f"  sleeping {sleep_s:.0f}s")
                time.sleep(sleep_s)

        browser.close()

    dbc.close()
    log.info(f"Done. {total} total images downloaded.")


def main():
    parser = argparse.ArgumentParser(description='Google Maps image scraper v1')
    parser.add_argument('--limit', type=int, default=0, help='Max cafes to process')
    parser.add_argument('--cafe-id', type=str, help='Process a single cafe by DB id')
    parser.add_argument('--force', action='store_true', help='Re-download even if images exist')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
