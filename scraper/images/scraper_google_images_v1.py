"""
scraper_google_images_v1.py
===========================

Standalone image scraper for Google Maps cafe entries.

Strategy:
  - Reads existing Google scraped_cafes from the DB (those without entries in the
    `images` table come first).
  - For each cafe, opens its stored Google Maps URL in a fresh Playwright
    browser context, dismisses the consent popup, and extracts all
    googleusercontent.com images from the rendered DOM.
  - Writes downloaded images to data/seoul/google/{safe_id}/images/ and
    inserts rows into the `images` table (same schema as Kakao v3).
  - Sleeps 60–90s between scraped_cafes to avoid reCAPTCHA.
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

try:
    from stem import Signal
    from stem.control import Controller
    import logging as _logging
    _logging.getLogger('stem').setLevel(_logging.WARNING)
    _STEM_AVAILABLE = True
except ImportError:
    _STEM_AVAILABLE = False

from utils import DATA_DIR, normalize_provider_id
from db_client import DBClient
from disk_check import check_disk_limit, DiskLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_HERE, '..', 'log', 'scraper_google_images_v1.log')),
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

BROWSER_ARGS = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-zygote']

TOR_SOCKS = "socks5://127.0.0.1:9050"
TOR_CONTROL_PORT = 9051
# Optional extra proxies: set env var SCRAPER_PROXIES as comma-separated list
# e.g. "socks5://1.2.3.4:1080,http://5.6.7.8:3128"
_EXTRA_PROXIES = [p.strip() for p in os.environ.get("SCRAPER_PROXIES", "").split(",") if p.strip()]


class ProxyManager:
    """
    Cycles through: no proxy → Tor → extra proxies → Tor (NEWNYM) → repeat.
    On each CAPTCHA call rotate() to get a fresh identity.
    """

    def __init__(self):
        self._slots = [None, TOR_SOCKS] + _EXTRA_PROXIES
        self._idx = 0
        self._tor_available = self._check_tor()

    def _check_tor(self) -> bool:
        # Probe Tor via several lightweight, high-availability endpoints. The old
        # single httpbin.org check timed out intermittently even when Tor was up
        # (httpbin is frequently down / slow over Tor exits), causing a spurious
        # "will skip Tor slots" fallback. Try a few endpoints with a generous
        # timeout before concluding Tor is unreachable.
        probes = [
            ("https://check.torproject.org/api/ip", lambda j: j.get("IP")),
            ("http://httpbin.org/ip", lambda j: j.get("origin")),
            ("https://api.ipify.org?format=json", lambda j: j.get("ip")),
        ]
        proxies = {"http": TOR_SOCKS, "https": TOR_SOCKS}
        last_err = None
        for url, pick in probes:
            try:
                r = requests.get(url, proxies=proxies, timeout=20)
                r.raise_for_status()
                log.info(f"Tor reachable, exit IP: {pick(r.json())}")
                return True
            except Exception as e:
                last_err = e
        log.warning(f"Tor SOCKS not reachable: {last_err} — will skip Tor slots")
        return False

    def _newnym(self):
        """Ask Tor for a new circuit."""
        if not _STEM_AVAILABLE:
            return
        try:
            with Controller.from_port(port=TOR_CONTROL_PORT) as ctrl:
                ctrl.authenticate()
                ctrl.signal(Signal.NEWNYM)
            log.info("Tor NEWNYM sent — new circuit")
            time.sleep(5)  # allow circuit to establish
        except Exception as e:
            log.warning(f"NEWNYM failed: {e}")

    @property
    def current(self):
        """Current proxy string or None (direct)."""
        slot = self._slots[self._idx % len(self._slots)]
        if slot == TOR_SOCKS and not self._tor_available:
            return None
        return slot

    def rotate(self):
        """Advance to next proxy slot; request new Tor circuit when entering Tor slot."""
        self._idx += 1
        slot = self._slots[self._idx % len(self._slots)]
        if slot == TOR_SOCKS:
            self._newnym()
        label = slot if slot else "direct"
        log.info(f"Proxy rotated → {label}")
        return self.current

    def playwright_proxy(self):
        """Playwright proxy dict or None."""
        p = self.current
        return {"server": p} if p else None

    def requests_proxies(self):
        """requests proxies dict or None."""
        p = self.current
        return {"http": p, "https": p} if p else None


PROXY_STATS_FILE = os.path.join(_HERE, "google-proxy-stats.json")


class ProxyStats:
    """
    Appends outcome events to google-proxy-stats.json.
    Schema per event:
      ts        – ISO timestamp
      proxy     – proxy label ("direct", "tor", or URL)
      cafe_id   – which cafe
      outcome   – "captcha" | "success" | "nav_error" | "no_images"
      images    – int (only for "success")
    Aggregated summary per proxy kept in "summary" key for quick reading.
    """

    def __init__(self):
        if os.path.exists(PROXY_STATS_FILE):
            try:
                with open(PROXY_STATS_FILE) as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {"summary": {}, "events": []}
        else:
            self._data = {"summary": {}, "events": []}

    def _label(self, proxy: str | None) -> str:
        if proxy is None:
            return "direct"
        if proxy == TOR_SOCKS:
            return "tor"
        return proxy

    def record(self, proxy, cafe_id: str, outcome: str, images: int = 0):
        label = self._label(proxy)
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "proxy": label,
            "cafe_id": cafe_id,
            "outcome": outcome,
        }
        if outcome == "success":
            event["images"] = images

        self._data["events"].append(event)

        s = self._data["summary"].setdefault(label, {
            "captcha": 0, "success": 0, "nav_error": 0, "no_images": 0, "images_total": 0
        })
        s[outcome] = s.get(outcome, 0) + 1
        if outcome == "success":
            s["images_total"] += images

        self._save()
        log.debug(f"ProxyStats: {label} {outcome} ({cafe_id})")

    def _save(self):
        try:
            with open(PROXY_STATS_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            log.warning(f"ProxyStats save failed: {e}")

    def log_summary(self):
        log.info("=== Proxy stats summary ===")
        for proxy, s in self._data["summary"].items():
            log.info(
                f"  {proxy:40s}  captcha={s.get('captcha',0):3d}"
                f"  success={s.get('success',0):3d}"
                f"  no_images={s.get('no_images',0):3d}"
                f"  nav_err={s.get('nav_error',0):3d}"
                f"  imgs={s.get('images_total',0)}"
            )


class PageDead(Exception):
    """Renderer process crashed — create a new page."""


class BrowserDead(Exception):
    """Browser process crashed — restart the browser."""


def _classify_nav_error(err: str):
    if any(s in err for s in ('Connection closed', 'Browser closed', 'pipe closed', 'browser has been closed')):
        raise BrowserDead(err)
    if any(s in err for s in ('Page crashed', 'Target closed')):
        raise PageDead(err)


def _sigterm(sig, frame):
    log.info("SIGTERM received — finishing current cafe then exiting.")
    _shutdown.set()


signal.signal(signal.SIGTERM, _sigterm)


def dismiss_consent(page):
    try:
        page.wait_for_timeout(2000)
        
        for jsname in ['b3VHJd', 'tWT92d']:
            try:
                btn = page.locator(f"button[jsname='{jsname}']")
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    return
            except Exception:
                pass

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


class CaptchaHit(Exception):
    """CAPTCHA detected — caller should rotate proxy and rebuild browser."""


def process_cafe(dbc, page, session, cafe_id, provider_id, cafe_url, proxy, stats, force=False):
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
        log.warning(f"  {cafe_id}: navigation error: {e!s:.120}")
        stats.record(proxy, cafe_id, "nav_error")
        _classify_nav_error(str(e))  # raises PageDead or BrowserDead if applicable
        return 0

    dismiss_consent(page)
    page.wait_for_timeout(3000)

    if is_captcha(page):
        log.warning(f"  CAPTCHA detected on {cafe_id}")
        stats.record(proxy, cafe_id, "captcha")
        raise CaptchaHit(cafe_id)

    # Lazy-load the photos region in the place panel.
    for _ in range(3):
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(600)

    # Extract only genuine place photos from the overview panel.
    #
    # Google Maps renders three kinds of googleusercontent <img> on a place page,
    # all sharing the same /gps-cs-s/ host so URL alone can't separate them:
    #   1. real place photos  — aria-label "Photo N of M", "Photo of <place>",
    #                            or inside a region "Photos of <place>"
    #   2. reviewer avatars    — served from /a/ or /a-/ paths, 32x32
    #   3. "similar places" carousel — aria-label "<OtherPlace> · X stars · N reviews",
    #                            wrapped in a role=link tile (these are OTHER cafes,
    #                            the root cause of cross-brand image leakage)
    # Keep (1), drop (2) and (3). Do NOT open the fullscreen gallery — it switches
    # to a different DOM that exposes no <img> elements.
    imgs = page.evaluate(r"""() => {
        const out = [];
        const seen = new Set();
        for (const im of document.querySelectorAll('img')) {
            let src = im.src || '';
            if (!src.includes('googleusercontent.com') && !src.includes('ggpht.com')) continue;
            if (src.includes('/a/') || src.includes('/a-/')) continue;       // reviewer avatar
            const r = im.getBoundingClientRect();
            if (r.width < 100 || r.height < 100) continue;                   // icon / avatar size
            const labels = [];
            let role_link = false, el = im;
            for (let i = 0; i < 7 && el; i++) {
                const al = el.getAttribute && el.getAttribute('aria-label');
                if (al) labels.push(al);
                if (el.getAttribute && el.getAttribute('role') === 'link') role_link = true;
                el = el.parentElement;
            }
            if (role_link) continue;                                         // similar-places tile
            if (labels.some(a => a.includes(' · ') && /star|review/i.test(a))) continue;
            const isPhoto = labels.some(a => /^Photo \d+ of \d+/.test(a))
                         || labels.some(a => a.startsWith('Photos of '))
                         || labels.some(a => a.startsWith('Photo of '));
            if (!isPhoto) continue;
            src = src.replace(/=w\d+-h\d+.*$/, '=w800-h600');
            if (seen.has(src)) continue;
            seen.add(src);
            out.push(src);
        }
        return out.slice(0, 30);
    }""")
    if not imgs:
        log.info(f"  {cafe_id}: no images found on page")
        stats.record(proxy, cafe_id, "no_images")
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
        local_path = f"/images/google/{safe_id}/images/{fname}"
        photo_id = f"{cafe_id}_{idx}"

        row_exists = dbc.fetchone(
            'SELECT 1 FROM images WHERE cafe_id=? AND photo_id=?',
            (cafe_id, photo_id)
        )

        # Only skip when the file AND its row are both present. A file with no row
        # is an orphan, and photo_id/fname are positional — the file sitting at this
        # index was written by an earlier pass and need not be imgs[idx] any more, so
        # pairing it with today's URL would record a wrong image_url. Re-download it
        # instead (same reasoning as scraper_kakao_images_v3).
        if os.path.exists(save_path) and row_exists and not force:
            downloaded += 1
            continue

        try:
            check_disk_limit()
        except DiskLimitExceeded as e:
            log.warning(str(e))
            break

        if download_image(session, img_url, save_path):
            file_size = os.path.getsize(save_path) if os.path.exists(save_path) else 0
            if file_size == 0:
                log.warning(f"  {cafe_id}: file missing/empty after download, skipping DB insert: {save_path}")
                continue
            downloaded += 1
            # `images` has no UNIQUE(cafe_id, photo_id), so INSERT OR REPLACE would
            # append a duplicate rather than replace. Update in place when re-fetching.
            if row_exists:
                dbc.execute('''
                    UPDATE images SET local_path=?, image_url=?, file_size=?
                    WHERE cafe_id=? AND photo_id=?
                ''', (local_path, img_url, file_size, cafe_id, photo_id))
            else:
                belongs_to = dbc.fetchval(
                    'SELECT belongs_to_cafe_id FROM scraped_cafes WHERE id = ?', (cafe_id,)
                )
                dbc.execute('''
                    INSERT INTO images
                      (cafe_id, provider, local_path, image_url, photo_id, belongs_to_cafe_id, file_size)
                    VALUES (?,?,?,?,?,?,?)
                ''', (cafe_id, 'google', local_path, img_url, photo_id, belongs_to, file_size))

    log.info(f"  {cafe_id}: {downloaded}/{len(imgs)} downloaded")
    if downloaded > 0:
        stats.record(proxy, cafe_id, "success", images=downloaded)
    return downloaded


def run(args):
    dbc = DBClient()

    if args.cafe_id:
        rows = dbc.fetchall(
            'SELECT id, provider_id, url FROM scraped_cafes WHERE id=? AND provider=?',
            (args.cafe_id, 'google')
        )
    else:
        rows = dbc.fetchall('''
            SELECT c.id, c.provider_id, c.url FROM scraped_cafes c
            WHERE c.provider = 'google'
            ORDER BY
                (SELECT COUNT(*) FROM images i WHERE i.cafe_id = c.id) ASC,
                c.id ASC
        ''')

    if args.limit > 0:
        rows = rows[:args.limit]

    log.info(f"Processing {len(rows)} Google scraped_cafes")

    proxy_mgr = ProxyManager()
    stats = ProxyStats()

    def make_session():
        s = requests.Session()
        ua = random.choice(USER_AGENTS)
        s.headers.update({'User-Agent': ua, 'Referer': 'https://www.google.com/'})
        proxies = proxy_mgr.requests_proxies()
        if proxies:
            s.proxies.update(proxies)
        return s

    total = 0
    consecutive_crashes = 0
    consecutive_captchas = 0
    MAX_CRASHES = 5
    MAX_CAPTCHAS = len(proxy_mgr._slots) + 1  # give up after exhausting all proxies

    with sync_playwright() as pw:
        def make_browser():
            b = pw.chromium.launch(headless=True, args=BROWSER_ARGS)
            c = b.new_context(
                user_agent=random.choice(USER_AGENTS),
                locale='en-US',
                proxy=proxy_mgr.playwright_proxy(),
            )
            p = c.new_page()
            warmup(p)
            return b, c, p

        browser, ctx, page = make_browser()
        session = make_session()

        i = 0
        while i < len(rows) and not _shutdown.is_set():
            cafe_id, provider_id, cafe_url = rows[i]

            try:
                n = process_cafe(dbc, page, session, cafe_id, provider_id, cafe_url,
                                 proxy=proxy_mgr.current, stats=stats, force=args.force)
                total += n
                consecutive_crashes = 0
                consecutive_captchas = 0
                i += 1
            except CaptchaHit:
                consecutive_captchas += 1
                if consecutive_captchas >= MAX_CAPTCHAS:
                    log.error(f"CAPTCHA on all {consecutive_captchas} proxy slots — giving up.")
                    break
                new_proxy = proxy_mgr.rotate()
                log.warning(f"  CAPTCHA #{consecutive_captchas} — proxy → {new_proxy or 'direct'}, rebuilding browser")
                try:
                    browser.close()
                except Exception:
                    pass
                # brief backoff so Tor circuit stabilises / IP ban expires
                time.sleep(random.uniform(15, 30))
                browser, ctx, page = make_browser()
                session = make_session()
                continue  # retry same cafe
            except PageDead as e:
                consecutive_crashes += 1
                log.warning(f"Page crash #{consecutive_crashes} on {cafe_id}: {e!s:.80} — new page")
                if consecutive_crashes >= MAX_CRASHES:
                    log.error(f"{consecutive_crashes} consecutive crashes — giving up.")
                    break
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    page = ctx.new_page()
                    warmup(page)
                except Exception:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser, ctx, page = make_browser()
                time.sleep(5)
                continue  # retry same cafe
            except BrowserDead as e:
                consecutive_crashes += 1
                log.warning(f"Browser crash #{consecutive_crashes} on {cafe_id}: {e!s:.80} — restarting")
                if consecutive_crashes >= MAX_CRASHES:
                    log.error(f"{consecutive_crashes} consecutive crashes — giving up.")
                    break
                try:
                    browser.close()
                except Exception:
                    pass
                time.sleep(5)
                browser, ctx, page = make_browser()
                continue  # retry same cafe
            except DiskLimitExceeded as e:
                log.warning(str(e))
                break
            except Exception as e:
                # Catch-all: an uncaught playwright/runtime error (e.g. TargetClosedError
                # from a page that died mid-wait) must not crash the whole scraper.
                # Rebuild the browser, skip the offending cafe, keep going.
                consecutive_crashes += 1
                log.warning(f"Unexpected error #{consecutive_crashes} on {cafe_id}: "
                            f"{type(e).__name__}: {e!s:.80} — rebuilding, skipping cafe")
                if consecutive_crashes >= MAX_CRASHES:
                    log.error(f"{consecutive_crashes} consecutive crashes — giving up.")
                    break
                try:
                    browser.close()
                except Exception:
                    pass
                time.sleep(5)
                browser, ctx, page = make_browser()
                session = make_session()
                i += 1  # skip the cafe that triggered the error
                continue

            if (i) % 10 == 0:
                log.info(f"Progress: {i}/{len(rows)} scraped_cafes, {total} images total")

            if i < len(rows) and not _shutdown.is_set():
                sleep_s = random.uniform(*SLEEP_BETWEEN_CAFES)
                log.info(f"  sleeping {sleep_s:.0f}s")
                time.sleep(sleep_s)

        try:
            browser.close()
        except Exception:
            pass

    dbc.close()
    stats.log_summary()
    log.info(f"Done. {total} total images downloaded.")


def main():
    parser = argparse.ArgumentParser(description='Google Maps image scraper v1')
    parser.add_argument('--limit', type=int, default=0, help='Max scraped_cafes to process')
    parser.add_argument('--cafe-id', type=str, help='Process a single cafe by DB id')
    parser.add_argument('--force', action='store_true', help='Re-download even if images exist')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
