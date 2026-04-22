"""
Headless investigation: capture ALL network requests on Kakao place photo page.
Discovers the pagination/API structure for photos.

Usage:
    cd scraper && source ../venv/bin/activate
    python investigate_kakao_photos_headless.py [place_id]
"""
import sys
import json
import time
import os
from playwright.sync_api import sync_playwright

PLACE_ID = sys.argv[1] if len(sys.argv) > 1 else "21340017"

def main():
    captured_api = []
    all_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844}
        )
        page = context.new_page()

        def on_response(resp):
            url = resp.url
            all_requests.append(f"{resp.status} {url}")
            # Capture any kakao API call (broad net)
            if 'kakao' in url.lower() or 'daumcdn' in url.lower():
                if any(k in url.lower() for k in ['photo', 'image', 'img', 'review', 'media', 'place', 'api']):
                    try:
                        body = resp.json()
                        captured_api.append({'url': url, 'status': resp.status, 'body': body})
                    except Exception:
                        pass

        page.on("response", on_response)

        # First load the main place page
        url1 = f"https://place.map.kakao.com/{PLACE_ID}"
        print(f"[1] Loading main place page: {url1}")
        page.goto(url1, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(2000)

        # Then navigate to photoview section
        url2 = f"https://place.map.kakao.com/{PLACE_ID}#photoview"
        print(f"[2] Navigating to photoview: {url2}")
        page.goto(url2, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(3000)

        # Scroll to trigger lazy loads / infinite scroll
        print("[3] Scrolling page to trigger pagination...")
        for i in range(8):
            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(1000)

        # Check for photo count text
        count_hints = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('*'))
                .filter(e => {
                    const t = e.innerText || '';
                    return /\\d+/.test(t) && t.length < 50 && e.children.length < 3;
                })
                .slice(0, 20)
                .map(e => ({ tag: e.tagName, text: (e.innerText||'').trim(), cls: e.className }));
        }""")

        # Get all images
        imgs = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('img')).map(img => ({
                src: img.src,
                dataSrc: img.getAttribute('data-src'),
                cls: img.className,
                alt: img.alt,
                width: img.naturalWidth,
                height: img.naturalHeight
            })).filter(i => (i.src && !i.src.includes('data:')) || i.dataSrc);
        }""")

        # Get all anchor tags with photo-related hrefs
        links = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a'))
                .map(a => ({ href: a.href, text: (a.innerText||'').trim(), cls: a.className }))
                .filter(a => a.href && (a.href.includes('photo') || a.href.includes('image') || a.href.includes('review')));
        }""")

        # Full HTML dump for analysis
        html_snippet = page.evaluate("() => document.body.innerHTML.substring(0, 3000)")

        browser.close()

    print("\n" + "="*60)
    print(f"KAKAO PHOTO INVESTIGATION — place_id={PLACE_ID}")
    print("="*60)

    print(f"\n[API Responses captured: {len(captured_api)}]")
    for c in captured_api:
        print(f"\n  URL: {c['url']}")
        print(f"  Body preview: {json.dumps(c['body'], ensure_ascii=False)[:500]}")

    print(f"\n[Images on page: {len(imgs)}]")
    for img in imgs[:20]:
        print(f"  src={img['src'][:100]}  dataSrc={str(img['dataSrc'])[:80]}  cls={img['cls'][:40]}")

    print(f"\n[Photo-related links: {len(links)}]")
    for lnk in links[:15]:
        print(f"  {lnk['href'][:100]}  text={lnk['text'][:30]}")

    print(f"\n[Count hints on page:]")
    for h in count_hints[:15]:
        print(f"  <{h['tag']} class={h['cls'][:30]}> {h['text']}")

    print(f"\n[All requests ({len(all_requests)}):]")
    for r in all_requests:
        print(f"  {r}")

    print(f"\n[HTML snippet (first 2000 chars):]")
    print(html_snippet[:2000])

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
