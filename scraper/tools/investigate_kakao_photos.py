"""
Investigation script: Capture all network requests on Kakao place photo page.
Run this to discover the pagination API for photos.

Usage:
    cd scraper && source ../venv/bin/activate
    python investigate_kakao_photos.py [place_id]

Default place_id: 21340017 (the example from the user)
"""
import sys
import json
import time
from playwright.sync_api import sync_playwright

PLACE_ID = sys.argv[1] if len(sys.argv) > 1 else "21340017"
URL = f"https://place.map.kakao.com/{PLACE_ID}#photoview"

captured = []

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # visible so we can watch
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844}
        )
        page = context.new_page()

        def on_response(resp):
            url = resp.url
            # Capture anything that looks like a photo/image API call
            keywords = ['photo', 'image', 'img', 'media', 'gallery', 'review']
            if any(k in url.lower() for k in keywords) and 'kakao' in url.lower():
                try:
                    body = resp.json()
                    captured.append({'url': url, 'status': resp.status, 'body': body})
                    print(f"\n[API] {url}")
                    print(json.dumps(body, ensure_ascii=False, indent=2)[:800])
                except Exception:
                    captured.append({'url': url, 'status': resp.status, 'body': None})
                    print(f"[non-JSON] {url}")

        def on_request(req):
            url = req.url
            keywords = ['photo', 'image', 'img', 'media', 'gallery', 'review']
            if any(k in url.lower() for k in keywords):
                print(f"[REQ] {req.method} {url}")

        page.on("response", on_response)
        page.on("request", on_request)

        print(f"Navigating to {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(4000)

        # Try scrolling to trigger lazy load / infinite scroll
        print("\n--- Scrolling to trigger pagination ---")
        for i in range(5):
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1500)
            print(f"Scroll {i+1}/5")

        # Check if there's a "more photos" button or count
        try:
            count_text = page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('*'));
                const found = els.filter(e => e.innerText && /\\d+\\s*(장|photos?|더보기)/i.test(e.innerText) && e.children.length < 3);
                return found.map(e => ({ tag: e.tagName, text: e.innerText.trim(), class: e.className }));
            }""")
            print(f"\n--- Photo count hints ---")
            for item in count_text[:10]:
                print(item)
        except Exception as e:
            print(f"Count check failed: {e}")

        # Dump all image src/data-src on the page
        try:
            imgs = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('img')).map(img => ({
                    src: img.src,
                    dataSrc: img.getAttribute('data-src'),
                    dataOriginal: img.getAttribute('data-original'),
                    class: img.className,
                    alt: img.alt
                })).filter(i => i.src || i.dataSrc);
            }""")
            print(f"\n--- Images on page ({len(imgs)}) ---")
            for img in imgs[:30]:
                print(img)
        except Exception as e:
            print(f"Image dump failed: {e}")

        # Dump the full page URL (after redirects)
        print(f"\nFinal URL: {page.url}")

        # Save full captured API calls
        print(f"\n--- Captured {len(captured)} API responses ---")
        for c in captured:
            print(f"  {c['status']} {c['url']}")

        # Keep browser open for manual inspection
        print("\nBrowser is open. Press Enter to close.")
        input()
        browser.close()

if __name__ == "__main__":
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
