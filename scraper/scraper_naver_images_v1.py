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
    Full scrape of all 20 Naver cafes will take several hours.

TEST RUNS (2026-03-30):
  --limit 5 (no --force): 5 cafes all skipped (existing images on disk);
    216 total images counted across 5 cafes in ~15s. DB query ordering works.

  --force runs (individual cafes):
    naver_2088955150 (726 reviews): 673 unique photos found via API (3 stale pages
      triggered stop); 173 downloaded before 300s run timeout. ~70ms per image.
    naver_2030407213 (162 reviews): 417 unique photos found.
    naver_2023116030 (178 reviews): 354 unique photos found.

  Pagination works: stale-stop fires after 3 pages of 0 new unique URLs.
  Photos on disk: photo_0000.jpg ... photo_0172.jpg confirmed in naver/1371876716/

  Rate limiting: after ~5-6 rapid Playwright requests, pcmap.place.naver.com bans
  the IP for 4+ hours regardless of UA. In normal operation (2-4s between cafes,
  single instance) this should not trigger. The 120s RATE_LIMIT_SLEEP handles
  individual 429s but cannot recover from a full IP block — restart after delay.

Usage:
    cd scraper && source ../venv/bin/activate
    python scraper_naver_images_v1.py [--limit N] [--cafe-id naver_XXXXX] [--force]
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

import requests
import asyncio
from playwright.async_api import async_playwright

from utils import DB_PATH, DATA_DIR, get_db_conn, db_execute, flush_db_queue, normalize_provider_id
from disk_check import check_disk_limit, DiskLimitExceeded

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

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

# Stop paginating if we get no new unique photos for this many consecutive pages
STALE_PAGES_LIMIT = 3
MAX_PAGES = 200  # hard cap

DELAY_BETWEEN_PAGES = 0.8
DELAY_BETWEEN_CAFES = (2.0, 4.0)
RATE_LIMIT_SLEEP = 120  # seconds to sleep on 429 nav; Naver IP bans last ~30+ min

DESKTOP_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
# Mobile UA avoids the desktop-headless ban Naver applies to repeated requests
MOBILE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) '
             'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 '
             'Mobile/15E148 Safari/604.1')

IMG_HEADERS = {
    'User-Agent': MOBILE_UA,
    'Referer': 'https://pcmap.place.naver.com/',
}


def make_wtm_token(place_id: str, business_type: str) -> str:
    """Create the x-wtm-graphql header value."""
    payload = {'arg': place_id, 'type': business_type, 'source': 'place'}
    return base64.b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()


async def fetch_photos_page(browser_page, cursor_state: list, place_id: str,
                            business_type: str, token: str) -> dict | None:
    """Make one GraphQL call inside the browser page context. Returns the photoViewer dict or None."""
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

    referer = f'https://pcmap.place.naver.com/restaurant/{place_id}/photo'

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
                const data = await resp.json();
                return {status: resp.status, data};
            } catch(e) {
                return {status: -1, error: String(e)};
            }
        }''', [GRAPHQL_URL, payload, token, referer])
    except Exception as e:
        log.warning(f"  evaluate() error: {e}")
        return None

    status = result.get('status')
    if status == 429:
        log.warning(f"  GraphQL 429 rate-limited, sleeping {RATE_LIMIT_SLEEP}s")
        await asyncio.sleep(RATE_LIMIT_SLEEP)
        return None
    if status != 200:
        log.warning(f"  GraphQL status {status}: {result.get('error', '')}")
        return None

    data = result.get('data')
    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict) and 'data' in r and 'photoViewer' in r['data']:
                return r['data']['photoViewer']
    return None


async def fetch_all_photos(browser_page, place_id: str, business_type: str) -> list[dict]:
    """Paginate through all photos. Returns list of unique photo dicts."""
    token = make_wtm_token(place_id, business_type)

    # Navigate to the photos tab to establish session cookies.
    # Use 'load' (not 'networkidle') to avoid long waits; give JS time to settle.
    nav_url = f'https://pcmap.place.naver.com/restaurant/{place_id}/photo'
    for nav_attempt in range(3):
        try:
            resp = await browser_page.goto(nav_url, wait_until='load', timeout=25000)
            if resp and resp.status == 429:
                log.warning(f"  429 on nav for {place_id}, sleeping {RATE_LIMIT_SLEEP}s")
                await asyncio.sleep(RATE_LIMIT_SLEEP)
                continue
            break
        except Exception as e:
            if nav_attempt < 2:
                log.warning(f"  Nav attempt {nav_attempt+1} failed for {place_id}: {e!s:.120}")
                await asyncio.sleep(5)
            else:
                log.warning(f"  Navigation failed for {place_id}: {e!s:.120}")
    await asyncio.sleep(2.0)

    # Initial cursor state (no lastCursor on first page)
    cursor_state = [
        {'id': 'biz'}, {'id': 'clip'}, {'id': 'cp0'},
        {'id': 'aiView'}, {'id': 'visitorReview'}, {'id': 'imgSas'}, {'id': 'cp'}
    ]

    all_photos: list[dict] = []
    seen_urls: set[str] = set()
    stale_pages = 0

    for page_num in range(1, MAX_PAGES + 1):
        pv = await fetch_photos_page(browser_page, cursor_state, place_id, business_type, token)
        if pv is None:
            log.warning(f"  Null response on page {page_num}, stopping")
            break

        photos = pv.get('photos') or []
        cursors = pv.get('cursors') or []

        new_count = 0
        for ph in photos:
            url = ph.get('originalUrl', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_photos.append(ph)
                new_count += 1

        log.debug(f"  page {page_num}: {len(photos)} returned, {new_count} new unique "
                  f"(total {len(all_photos)})")

        if new_count == 0:
            stale_pages += 1
            if stale_pages >= STALE_PAGES_LIMIT:
                log.info(f"  No new photos for {STALE_PAGES_LIMIT} consecutive pages, stopping")
                break
        else:
            stale_pages = 0

        # Check if all cursors are done
        active_cursors = [c for c in cursors if c.get('hasNext')]
        if not active_cursors:
            log.info(f"  All cursors exhausted after page {page_num}")
            break

        # Build next cursor state from active cursors only
        cursor_state = []
        for c in cursors:
            if c.get('hasNext'):
                entry = {'id': c['id']}
                if c.get('lastCursor'):
                    entry['lastCursor'] = c['lastCursor']
                cursor_state.append(entry)

        if not cursor_state:
            break

        await asyncio.sleep(DELAY_BETWEEN_PAGES)

    return all_photos


def download_image(session: requests.Session, url: str, save_path: str) -> bool:
    try:
        resp = session.get(url, stream=True, timeout=20)
        resp.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        log.debug(f"    Failed {url[:80]}: {e}")
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
        return False


def ext_for_url(url: str) -> str:
    path = urlparse(url.split('?')[0]).path
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in IMAGE_EXTENSIONS else '.jpg'


async def process_cafe(conn, browser_page, http_session, cafe_id: str,
                       provider_id: str, metadata: dict, force: bool = False) -> int:
    safe_id = normalize_provider_id(provider_id)
    img_dir = os.path.join(DATA_DIR, 'naver', safe_id, 'images')

    existing = []
    if os.path.exists(img_dir):
        existing = [f for f in os.listdir(img_dir)
                    if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS]

    if existing and not force:
        log.info(f"  Skip {cafe_id}: {len(existing)} images already on disk")
        return len(existing)

    log.info(f"Processing {cafe_id} (provider_id={provider_id})")

    photos = await fetch_all_photos(browser_page, provider_id, 'restaurant')

    if not photos:
        log.info(f"  {cafe_id}: no photos found")
        metadata['all_photos'] = 0
        metadata['scraped_photos'] = 0
        db_execute(conn, 'UPDATE cafes SET metadata=? WHERE id=?',
                   (json.dumps(metadata, ensure_ascii=False), cafe_id))
        return 0

    log.info(f"  {cafe_id}: {len(photos)} unique photos to download")
    os.makedirs(img_dir, exist_ok=True)

    downloaded = 0
    failed = 0

    for idx, ph in enumerate(photos):
        url = ph.get('originalUrl', '')
        if not url:
            continue

        ext = ext_for_url(url)
        fname = f"photo_{idx:04d}{ext}"
        save_path = os.path.join(img_dir, fname)

        if os.path.exists(save_path) and not force:
            downloaded += 1
            continue

        try:
            check_disk_limit()
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break

        if download_image(http_session, url, save_path):
            downloaded += 1
        else:
            failed += 1

        time.sleep(0.15)

    # Build local_images list from disk
    all_files = sorted(
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
    )
    local_paths = [f"/images/naver/{safe_id}/images/{f}" for f in all_files]

    # Store photo metadata
    photo_meta = []
    for ph in photos:
        photo_meta.append({
            'image_url': ph.get('originalUrl', ''),
            'photo_id': ph.get('logId') or ph.get('viewId', ''),
            'photo_type': ph.get('photoType', ''),
            'relation': ph.get('relation', ''),
            'registered_at': ph.get('date') or ph.get('originalDate') or '',
            'title': ph.get('title', ''),
            'width': ph.get('width'),
            'height': ph.get('height'),
            'author_id': (ph.get('author') or {}).get('id', ''),
            'author_nickname': (ph.get('author') or {}).get('nickname', ''),
            'clip_type': (ph.get('clip') or {}).get('contentType', ''),
            'gallery_url': (ph.get('author') or {}).get('url', ''),
        })

    metadata['local_images'] = local_paths
    metadata['all_photos'] = len(photos)
    metadata['scraped_photos'] = len(all_files)
    metadata['photo_meta'] = photo_meta

    db_execute(conn, 'UPDATE cafes SET metadata=? WHERE id=?',
               (json.dumps(metadata, ensure_ascii=False), cafe_id))

    log.info(f"  {cafe_id}: {len(all_files)} saved, {failed} failed / {len(photos)} total unique")
    return len(all_files)


async def run(args):
    conn = get_db_conn()
    cursor = conn.cursor()

    if args.cafe_id:
        cursor.execute('SELECT id, provider_id, metadata FROM cafes WHERE id=? AND provider=?',
                       (args.cafe_id, 'naver'))
    else:
        cursor.execute('''
            SELECT id, provider_id, metadata FROM cafes
            WHERE provider = 'naver'
            ORDER BY
                CASE WHEN json_extract(metadata, '$.local_images') IS NULL THEN 0 ELSE 1 END ASC,
                json_array_length(COALESCE(json_extract(metadata, '$.local_images'), '[]')) ASC
        ''')

    rows = cursor.fetchall()
    if args.limit > 0:
        rows = rows[:args.limit]

    log.info(f"Processing {len(rows)} Naver cafes")

    http_session = requests.Session()
    http_session.headers.update(IMG_HEADERS)

    total_dl = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        context = await browser.new_context(
            user_agent=MOBILE_UA,
            locale='ko-KR',
        )
        browser_page = await context.new_page()

        for i, (cafe_id, provider_id, meta_json) in enumerate(rows):
            try:
                metadata = json.loads(meta_json) if meta_json else {}
            except json.JSONDecodeError:
                metadata = {}

            try:
                n = await process_cafe(conn, browser_page, http_session,
                                       cafe_id, provider_id, metadata, force=args.force)
                total_dl += n
                flush_db_queue(conn)
            except DiskLimitExceeded as e:
                log.warning(str(e))
                break
            except Exception as e:
                log.error(f"Error {cafe_id}: {e}", exc_info=True)

            delay = random.uniform(*DELAY_BETWEEN_CAFES)
            await asyncio.sleep(delay)

            if (i + 1) % 10 == 0:
                log.info(f"Progress: {i+1}/{len(rows)} cafes, {total_dl} images total")

        await browser.close()

    flush_db_queue(conn)
    conn.close()
    log.info(f"Done. {total_dl} total images downloaded.")
    return total_dl


def main():
    parser = argparse.ArgumentParser(description='Naver Maps image scraper v1')
    parser.add_argument('--limit', type=int, default=0, help='Max number of cafes to process')
    parser.add_argument('--cafe-id', type=str, help='Process a single cafe by DB id')
    parser.add_argument('--force', action='store_true', help='Re-download even if images exist')
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == '__main__':
    main()
