"""
scraper_naver_images_v1.py
==========================

WHAT WORKS:
  - API: POST https://pcmap-api.place.naver.com/graphql
    Operation: getPhotoViewerItems
    Variables: { input: { businessId, businessType, cursors: [...], ... } }
  - The API returns up to 120 photos per page across multiple source cursors:
      biz        — business-uploaded photos (ldb-phinf.pstatic.net)
      clip       — Naver Clip (short video platform) thumbnails
      cp0        — (not always present in response)
      aiView     — AI-curated interior/exterior views (blogfiles.pstatic.net)
      visitorReview — user review photos
      imgSas     — image search / SAS photos
      cp         — (not always present in response)
  - Each cursor tracks its own pagination position via lastCursor token.
    On the first call, pass all cursor IDs with no lastCursor.
    On subsequent calls, pass only the cursors still active (hasNext=True)
    with their lastCursor values.
  - Photo metadata per item: originalUrl, width, height, title, photoType,
    relation (e.g. "업체", "방문자리뷰"), date, author (id, nickname, url),
    clip (serviceType, createdAt), logId, viewId.
  - Pagination terminates when: ALL returned cursors show hasNext=False, OR
    no new unique URLs appeared in the last STALE_PAGES_LIMIT consecutive
    pages (guards against infinite loops from cursors that never stop).
  - Direct HTTP requests return 429. Requires a Playwright browser session
    to inherit Naver cookies and the correct Origin/Referer headers.
    API calls are made via page.evaluate() (fetch inside the browser context).
  - x-wtm-graphql header must be base64({"arg": place_id, "type": business_type,
    "source": "place"}). businessType defaults to "restaurant" for cafes.
  - Images are downloaded with a plain requests session using a desktop UA and
    Referer: https://pcmap.place.naver.com/

WHAT DID NOT WORK / LIMITATIONS:
  - Direct requests.post to the GraphQL endpoint returns 429 (rate-limited
    without valid Naver session cookies). Must use Playwright page context.
  - Some photo URLs use ldb-phinf.pstatic.net or blogfiles.pstatic.net — these
    download fine directly (unlike Kakao's Naver blog CDN issue).
  - clip cursor and visitorReview cursor never terminate (hasNext always True)
    even after all unique photos are exhausted — stale detection is essential.
  - A single Playwright browser is shared across all cafes in a run to avoid
    the overhead of launching a new browser per cafe.
  - businessType detection: the referer URL must use the correct type path
    (restaurant / place). We try "restaurant" first; if the page returns an
    empty photo list we fall back to "place".
  - Naver enforces a UA-level rate limit on pcmap.place.naver.com. Repeated
    rapid desktop-UA requests trigger a long ban (4+ hours). The fix: use a
    mobile iOS Safari UA for both the Playwright browser context and image
    downloads. MOBILE_UA = iPhone iOS 16 Safari. With the mobile UA, rapid
    requests to different places work without banning.
    The DELAY_BETWEEN_CAFES of 2-4s adds extra safety.
  - Even cafes with only ~30 reviews have 100-400 unique photos across all
    cursor types (biz + clips + visitor reviews + AI View + imgSas).

PAGINATION / STATE:
  - naver_scrape_state table tracks per-cafe cursor state, pages_fetched,
    business_type, stale_count, attempt_count, and status.
  - Each run picks a random cafe from the cohort with MIN(pages_fetched)
    among pending cafes, processes one GraphQL page, then moves on.
  - This spreads scraping evenly: all cafes get page 1 before any gets page 2.
  - Navigation (to establish Naver session cookies) happens once per cafe per
    session. Subsequent pages reuse the browser context's cookies.
  - Cafes returning 0 photos on page 1 are retried up to MAX_ATTEMPT_COUNT
    times before being flagged and skipped permanently.

Usage:
    cd scraper && source ../venv/bin/activate
    python scraper_naver_images_v1.py [--cafe-id naver_XXXXX] [--force]
"""

import os
import sys
import json
import time
import random
import logging
import argparse
import base64
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

import threading
import requests
import asyncio
from playwright.async_api import async_playwright

from utils import DATA_DIR, normalize_provider_id
from db_client import DBClient
from disk_check import check_disk_limit, DiskLimitExceeded
from image_utils import save_image

_shutdown = threading.Event()


def _sigterm(sig, frame):
    log.info("SIGTERM received — finishing current page then exiting.")
    _shutdown.set()


import signal as _signal_mod
_signal_mod.signal(_signal_mod.SIGTERM, _sigterm)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_naver_images_v1.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

GRAPHQL_URL = 'https://pcmap-api.place.naver.com/graphql'

PHOTO_QUERY = (
    'query getPhotoViewerItems($input: PhotoViewerInput) {\n'
    '  photoViewer(input: $input) {\n'
    '    cursors { id startIndex hasNext lastCursor __typename }\n'
    '    photos {\n'
    '      viewId originalUrl originalDate width height title text date\n'
    '      photoType relation logId\n'
    '      author { id nickname from url __typename }\n'
    '      clip { serviceType createdAt contentType __typename }\n'
    '      __typename\n'
    '    }\n'
    '    __typename\n'
    '  }\n'
    '}'
)

INITIAL_CURSORS = [
    {'id': 'biz'}, {'id': 'clip'}, {'id': 'cp0'},
    {'id': 'aiView'}, {'id': 'visitorReview'}, {'id': 'imgSas'}, {'id': 'cp'}
]

# Stop paginating if we get no new unique photos for this many consecutive pages
STALE_PAGES_LIMIT = 3
MAX_ATTEMPT_COUNT  = 3   # flag cafe after this many page-1 zero-result attempts

DELAY_BETWEEN_PAGES = 0.8
DELAY_BETWEEN_CAFES = (2.0, 4.0)
RATE_LIMIT_SLEEP    = 120  # seconds to sleep on 429; Naver IP bans last ~30+ min

# Mobile UA avoids the desktop-headless ban Naver applies to repeated requests
MOBILE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) '
             'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 '
             'Mobile/15E148 Safari/604.1')

IMG_HEADERS = {
    'User-Agent': MOBILE_UA,
    'Referer': 'https://pcmap.place.naver.com/',
}


def make_wtm_token(place_id: str, business_type: str) -> str:
    payload = {'arg': place_id, 'type': business_type, 'source': 'place'}
    return base64.b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()


# ── Scrape state table ────────────────────────────────────────────────────────

def init_scrape_state(dbc):
    """
    Create naver_scrape_state if absent, insert rows for every Naver cafe.
    Returns count of pending cafes.
    """
    dbc.execute('''
        CREATE TABLE IF NOT EXISTS naver_scrape_state (
            cafe_id        TEXT PRIMARY KEY,
            business_type  TEXT    DEFAULT NULL,
            cursor_state   TEXT    DEFAULT NULL,
            pages_fetched  INTEGER DEFAULT 0,
            stale_count    INTEGER DEFAULT 0,
            attempt_count  INTEGER DEFAULT 0,
            status         TEXT    DEFAULT 'pending',
            last_attempted TIMESTAMP,
            FOREIGN KEY (cafe_id) REFERENCES cafes(id)
        )
    ''')
    dbc.execute('''
        INSERT OR IGNORE INTO naver_scrape_state (cafe_id)
        SELECT id FROM cafes WHERE provider = 'naver'
    ''')

    pending   = dbc.fetchval("SELECT COUNT(*) FROM naver_scrape_state WHERE status='pending'")
    exhausted = dbc.fetchval("SELECT COUNT(*) FROM naver_scrape_state WHERE status='exhausted'")
    flagged   = dbc.fetchval("SELECT COUNT(*) FROM naver_scrape_state WHERE status='flagged'")
    log.info(f"Scrape state — pending:{pending}  exhausted:{exhausted}  flagged:{flagged}")
    return pending


def pick_random_pending_cafe(dbc):
    """
    Return random cafe with MIN(pages_fetched) among pending.
    Returns None when nothing is pending.
    """
    row = dbc.fetchone('''
        SELECT s.cafe_id, s.business_type, s.cursor_state,
               s.pages_fetched, s.stale_count, s.attempt_count,
               c.provider_id, c.metadata
        FROM   naver_scrape_state s
        JOIN   cafes c ON c.id = s.cafe_id
        WHERE  s.status = 'pending'
          AND  s.pages_fetched = (SELECT MIN(pages_fetched)
                                  FROM   naver_scrape_state
                                  WHERE  status = 'pending')
        ORDER BY RANDOM()
        LIMIT 1
    ''')
    return row


# ── GraphQL fetch (single page) ───────────────────────────────────────────────

async def _graphql_call(browser_page, cursor_state: list, place_id: str,
                        business_type: str, token: str) -> dict | None:
    """Raw single GraphQL call inside the browser context. Returns photoViewer dict or None."""
    payload = [{
        'operationName': 'getPhotoViewerItems',
        'variables': {
            'input': {
                'businessId': place_id,
                'businessType': business_type,
                'cursors': cursor_state,
                'excludeAuthorIds': [],
                'excludeSection': [],
                'excludeClipIds': [],
                'dateRange': ''
            }
        },
        'query': PHOTO_QUERY
    }]
    referer = f'https://pcmap.place.naver.com/{business_type}/{place_id}/photo'

    try:
        result = await browser_page.evaluate('''async (args) => {
            const [url, payload, token, referer] = args;
            try {
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'x-wtm-graphql': token,
                        'Referer': referer,
                        'Origin': 'https://pcmap.place.naver.com',
                    },
                    body: JSON.stringify(payload),
                });
                const text = await resp.text();
                let data;
                try { data = JSON.parse(text); }
                catch(e) { return {status: resp.status, error: 'JSON parse error', text: text.substring(0, 200)}; }
                return {status: resp.status, data};
            } catch(e) { return {status: -1, error: String(e)}; }
        }''', [GRAPHQL_URL, payload, token, referer])
    except Exception as e:
        log.warning(f"  evaluate() error: {e}")
        return None

    status = result.get('status')
    if status == 429:
        log.warning(f"  GraphQL 429, sleeping {RATE_LIMIT_SLEEP}s")
        await asyncio.sleep(RATE_LIMIT_SLEEP)
        return None
    if status != 200 or result.get('error'):
        log.warning(f"  GraphQL status {status}: {result.get('error', '')} {result.get('text', '')}")
        return None

    data = result.get('data')
    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict) and 'data' in r and 'photoViewer' in r['data']:
                return r['data']['photoViewer']
    return None


async def navigate_for_cafe(browser_page, place_id: str, business_type: str,
                             navigated_for: set) -> bool:
    """
    Navigate to the cafe's photo page to establish Naver session cookies.
    Skipped if already navigated for this (place_id, business_type) in this session.
    Returns True on success, False on failure.
    """
    key = (place_id, business_type)
    if key in navigated_for:
        return True

    nav_url = f'https://pcmap.place.naver.com/{business_type}/{place_id}/photo'
    for attempt in range(3):
        try:
            resp = await browser_page.goto(nav_url, wait_until='domcontentloaded', timeout=25000)
            if resp and resp.status == 429:
                log.warning(f"  429 on nav for {place_id}, sleeping {RATE_LIMIT_SLEEP}s")
                await asyncio.sleep(RATE_LIMIT_SLEEP)
                continue
            navigated_for.add(key)
            await asyncio.sleep(2.0)
            return True
        except Exception as e:
            if attempt < 2:
                log.warning(f"  Nav attempt {attempt+1} failed: {e!s:.100}")
                await asyncio.sleep(5)
            else:
                log.warning(f"  Navigation failed for {place_id}: {e!s:.100}")
    return False


async def fetch_one_page(browser_page, place_id: str, business_type: str,
                          cursor_state: list, navigated_for: set
                          ) -> tuple[list, list, bool]:
    """
    Navigate (once per session per cafe) then make one GraphQL call.
    Returns (photos, new_cursor_state, has_next).
    new_cursor_state is the active cursors list for the next call.
    has_next=False when all cursors exhausted.
    """
    if not await navigate_for_cafe(browser_page, place_id, business_type, navigated_for):
        return [], cursor_state, True  # nav failed; leave has_next=True so we retry later

    token = make_wtm_token(place_id, business_type)
    pv = await _graphql_call(browser_page, cursor_state, place_id, business_type, token)
    if pv is None:
        return [], cursor_state, True  # API error; retry later

    photos   = pv.get('photos') or []
    cursors  = pv.get('cursors') or []

    new_cursor_state = []
    for c in cursors:
        if c.get('hasNext'):
            entry = {'id': c['id']}
            if c.get('lastCursor'):
                entry['lastCursor'] = c['lastCursor']
            new_cursor_state.append(entry)

    has_next = bool(new_cursor_state)
    return photos, new_cursor_state, has_next


# ── Image download ────────────────────────────────────────────────────────────

def download_image(session: requests.Session, url: str) -> bytes | None:
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        log.debug(f"    Failed {url[:80]}: {e}")
        return None


# ── Per-page processing ───────────────────────────────────────────────────────

async def process_one_page(dbc, browser_page, http_session,
                            cafe_id: str, provider_id: str, metadata: dict,
                            business_type: str | None, cursor_state_json: str | None,
                            navigated_for: set) -> tuple[int, list, bool, str]:
    """
    Fetch one GraphQL page, download new images, write DB rows.
    Handles business_type detection (restaurant → place fallback) on first page.
    Returns (new_downloads, new_cursor_state, has_next, detected_business_type).
    """
    safe_id = normalize_provider_id(provider_id)
    img_dir = os.path.join(DATA_DIR, 'naver', safe_id, 'images')
    os.makedirs(img_dir, exist_ok=True)

    is_first_page = cursor_state_json is None
    cursor_state  = json.loads(cursor_state_json) if cursor_state_json else INITIAL_CURSORS
    detected_type = business_type or 'restaurant'

    # Business type detection on first page: try restaurant, fall back to place
    if is_first_page:
        photos, new_cursors, has_next = await fetch_one_page(
            browser_page, provider_id, 'restaurant', cursor_state, navigated_for
        )
        detected_type = 'restaurant'
        if not photos and not has_next:
            log.info(f"  {cafe_id}: no photos as restaurant, trying place type")
            # Force navigation to the place URL for fresh cookies
            navigated_for.discard((provider_id, 'restaurant'))
            photos, new_cursors, has_next = await fetch_one_page(
                browser_page, provider_id, 'place', INITIAL_CURSORS, navigated_for
            )
            detected_type = 'place'
    else:
        photos, new_cursors, has_next = await fetch_one_page(
            browser_page, provider_id, detected_type, cursor_state, navigated_for
        )

    # Collect unique URLs already in DB to skip duplicates
    db_count = dbc.fetchval('SELECT COUNT(*) FROM images WHERE cafe_id=?', (cafe_id,)) or 0
    idx = db_count  # next filename index

    all_local = list(metadata.get('local_images', []))
    new_downloads = 0
    failed = 0

    for ph in photos:
        url = ph.get('originalUrl', '')
        if not url:
            continue

        photo_id = ph.get('logId') or ph.get('viewId') or ''
        # Skip if already in DB
        if photo_id and dbc.fetchone(
            'SELECT 1 FROM images WHERE cafe_id=? AND photo_id=?', (cafe_id, photo_id)
        ):
            continue

        fname      = f"photo_{idx:04d}.jpg"
        save_path  = os.path.join(img_dir, fname)
        local_path = f"/images/naver/{safe_id}/images/{fname}"

        # File on disk but no DB row — backfill (always insert, even without photo_id)
        if os.path.exists(save_path):
            dbc.execute('''
                INSERT OR REPLACE INTO images
                  (cafe_id, provider, local_path, image_url, gallery_url,
                   photo_id, photo_type, registered_at, width, height)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            ''', (cafe_id, 'naver', local_path, url,
                  (ph.get('author') or {}).get('url', ''),
                  photo_id or f"{cafe_id}_{idx}",
                  ph.get('photoType', ''),
                  ph.get('date') or ph.get('originalDate') or '',
                  ph.get('width'), ph.get('height')))
            idx += 1
            new_downloads += 1
            if local_path not in all_local:
                all_local.append(local_path)
            continue

        try:
            check_disk_limit()
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break

        img_bytes = download_image(http_session, url)
        if img_bytes is not None:
            _, img_meta = save_image(img_bytes, save_path)
            idx += 1
            new_downloads += 1
            if local_path not in all_local:
                all_local.append(local_path)
            dbc.execute('''
                INSERT OR REPLACE INTO images
                  (cafe_id, provider, local_path, image_url, gallery_url,
                   photo_id, photo_type, registered_at, width, height, file_size)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ''', (cafe_id, 'naver', local_path, url,
                  (ph.get('author') or {}).get('url', ''),
                  photo_id or f"{cafe_id}_{idx}",
                  ph.get('photoType', ''),
                  ph.get('date') or ph.get('originalDate') or '',
                  img_meta['width'], img_meta['height'], img_meta['file_size']))
        else:
            failed += 1

        time.sleep(0.15)

    # Update cafe metadata
    metadata['local_images'] = all_local
    metadata['scraped_photos'] = db_count + new_downloads
    dbc.execute('UPDATE cafes SET metadata=? WHERE id=?',
                (json.dumps(metadata, ensure_ascii=False), cafe_id))

    log.info(f"  {cafe_id} [{detected_type}]: +{new_downloads} new, {failed} failed, has_next={has_next}")
    return new_downloads, new_cursors, has_next, detected_type


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(args):
    dbc     = DBClient()
    pending = init_scrape_state(dbc)

    if pending == 0:
        log.info("All cafes exhausted or flagged — nothing to do.")
        dbc.close()
        return

    http_session = requests.Session()
    http_session.headers.update(IMG_HEADERS)

    navigated_for: set = set()
    pages_fetched = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        context = await browser.new_context(user_agent=MOBILE_UA, locale='ko-KR')
        browser_page = await context.new_page()

        while not _shutdown.is_set():
            # ── Pick next cafe ────────────────────────────────────────────────
            if args.cafe_id:
                row = dbc.fetchone('''
                    SELECT s.cafe_id, s.business_type, s.cursor_state,
                           s.pages_fetched, s.stale_count, s.attempt_count,
                           c.provider_id, c.metadata
                    FROM   naver_scrape_state s
                    JOIN   cafes c ON c.id = s.cafe_id
                    WHERE  s.cafe_id = ? AND s.status = 'pending'
                ''', (args.cafe_id,))
            else:
                row = pick_random_pending_cafe(dbc)

            if row is None:
                log.info("No pending cafes — done.")
                break

            (cafe_id, business_type, cursor_state_json,
             pf, stale_count, attempt_count,
             provider_id, meta_json) = row

            try:
                metadata = json.loads(meta_json) if meta_json else {}
            except json.JSONDecodeError:
                metadata = {}

            log.info(f"Processing {cafe_id} page {pf+1} "
                     f"(type={business_type or '?'}, attempts={attempt_count})")

            dbc.execute(
                "UPDATE naver_scrape_state SET last_attempted=CURRENT_TIMESTAMP WHERE cafe_id=?",
                (cafe_id,)
            )

            # ── Fetch + download one page ─────────────────────────────────────
            try:
                new_dl, new_cursors, has_next, detected_type = await process_one_page(
                    dbc, browser_page, http_session,
                    cafe_id, provider_id, metadata,
                    business_type, cursor_state_json, navigated_for
                )
            except DiskLimitExceeded as e:
                log.warning(str(e))
                break
            except Exception as e:
                log.error(f"Error {cafe_id}: {e}", exc_info=True)
                await asyncio.sleep(random.uniform(*DELAY_BETWEEN_CAFES))
                continue

            pages_fetched += 1

            new_stale = (stale_count + 1) if new_dl == 0 else 0
            is_first = cursor_state_json is None

            if not has_next or new_stale >= STALE_PAGES_LIMIT:
                if is_first and new_dl == 0:
                    new_attempts = attempt_count + 1
                    if new_attempts >= MAX_ATTEMPT_COUNT:
                        log.info(f"  {cafe_id}: flagged after {new_attempts} zero-result attempts")
                        dbc.execute(
                            "UPDATE naver_scrape_state SET status='flagged', attempt_count=? WHERE cafe_id=?",
                            (new_attempts, cafe_id))
                    else:
                        log.info(f"  {cafe_id}: zero results, attempt {new_attempts}/{MAX_ATTEMPT_COUNT}")
                        dbc.execute(
                            "UPDATE naver_scrape_state SET attempt_count=?, pages_fetched=? WHERE cafe_id=?",
                            (new_attempts, pf + 1, cafe_id))
                else:
                    reason = "stale" if new_stale >= STALE_PAGES_LIMIT else "cursors exhausted"
                    log.info(f"  {cafe_id}: exhausted ({reason})")
                    dbc.execute(
                        "UPDATE naver_scrape_state SET status='exhausted', pages_fetched=?, business_type=? WHERE cafe_id=?",
                        (pf + 1, detected_type, cafe_id))
            else:
                dbc.execute('''
                    UPDATE naver_scrape_state
                    SET cursor_state=?, pages_fetched=?, stale_count=?,
                        attempt_count=0, business_type=?
                    WHERE cafe_id=?
                ''', (json.dumps(new_cursors), pf + 1, new_stale, detected_type, cafe_id))

            if pages_fetched % 50 == 0:
                p = dbc.fetchval("SELECT COUNT(*) FROM naver_scrape_state WHERE status='pending'")
                log.info(f"Progress: {pages_fetched} pages fetched, {p} cafes still pending")

            await asyncio.sleep(random.uniform(*DELAY_BETWEEN_CAFES))

        await browser.close()

    dbc.close()
    log.info("Done.")


def main():
    parser = argparse.ArgumentParser(description='Naver Maps image scraper v1')
    parser.add_argument('--cafe-id', type=str, help='Process a single cafe by DB id')
    parser.add_argument('--force', action='store_true', help='Re-download even if images exist')
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
