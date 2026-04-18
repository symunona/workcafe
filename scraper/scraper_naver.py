import os
import json
import time
import random
import argparse
import threading
import signal
from playwright.sync_api import sync_playwright
from utils import init_db, get_spiral_coordinates, DATA_DIR, CENTER_LAT, CENTER_LON, normalize_provider_id, check_if_done
from db_client import DBClient
from download_utils import download_image

_shutdown = threading.Event()


def _sigterm(sig, frame):
    print("SIGTERM received — finishing current grid then exiting.", flush=True)
    _shutdown.set()


signal.signal(signal.SIGTERM, _sigterm)


def watchdog(timeout):
    print(f"Watchdog triggered! Process hung for more than {timeout} seconds. Exiting.")
    os._exit(1)

def scrape_naver_grid(context, dbc, grid_x, grid_y, lat, lon):
    provider = 'naver'
    row = dbc.fetchone('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?',
                       (grid_x, grid_y, provider))
    if row and row[0] == 'completed':
        pass # print(f"Skipping Naver grid ({grid_x}, {grid_y}), already completed.")
        return True

    api_data = []

    def handle_response(response):
        if "api/search/allSearch" in response.url:
            try:
                data = response.json()
                api_data.append(data)
            except Exception:
                pass

    page = context.new_page()
    page.on("response", handle_response)

    print(f"Scraping Naver grid ({grid_x}, {grid_y}) at {lat}, {lon}")

    try:
        coord_url = f"https://map.naver.com/p?c=15.00,0,0,0,dh&lat={lat}&lng={lon}"
        page.goto(coord_url)
        page.wait_for_timeout(2000)

        page.fill("input.input_search", "카페")
        page.press("input.input_search", "Enter")

        page.wait_for_timeout(5000)

        page.remove_listener("response", handle_response)

        if not api_data:
            print(f"No API data intercepted for grid ({grid_x}, {grid_y}). Might be blocked or no results.")
            return False

        data = api_data[-1]

        if 'result' not in data or 'place' not in data['result'] or 'list' not in data['result']['place']:
            print("Unexpected JSON structure.")
            return False

        places = data['result']['place']['list']
        print(f"Found {len(places)} cafes in this grid.")

        for place in places:
            provider_id = str(place.get('id', ''))
            if not provider_id:
                continue

            name = place.get('name', 'Unknown')
            p_lat = place.get('y', '')
            p_lon = place.get('x', '')
            address = place.get('roadAddress', place.get('address', ''))

            cafe_url = f"https://map.naver.com/p/entry/place/{provider_id}"

            images = []
            try:
                detail_page = page.context.new_page()
                pcmap_url = f"https://pcmap.place.naver.com/place/{provider_id}/photo"
                detail_page.goto(pcmap_url, wait_until="domcontentloaded")
                detail_page.wait_for_timeout(3000)

                images = detail_page.evaluate('''() => {
                    const imgElements = document.querySelectorAll('img');
                    const urls = [];
                    imgElements.forEach(img => {
                        let src = img.src;
                        let dataSrc = img.getAttribute('data-src');
                        let finalSrc = dataSrc || src;
                        if (finalSrc && (finalSrc.includes('search.pstatic.net') || finalSrc.includes('phinf.pstatic.net'))) {
                            urls.push(finalSrc);
                        }
                    });
                    return Array.from(new Set(urls)).slice(0, 10);
                }''')

                detail_page.close()
            except Exception as e:
                print(f"Error fetching detail page for {name}: {e}")
                try:
                    detail_page.close()
                except:
                    pass
            print(f"Found {len(images)} images for {name}")

            global_id = f"{provider}_{provider_id}"
            safe_id = normalize_provider_id(provider_id)
            cafe_dir = os.path.join(DATA_DIR, provider, safe_id)
            images_dir = os.path.join(cafe_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)

            local_images = []
            for i, img_url in enumerate(images):
                ext = img_url.split('?')[0].split('.')[-1]
                if ext.lower() not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                    ext = 'jpg'
                filename = f"img_{i}.{ext}"
                filepath = os.path.join(images_dir, filename)
                if download_image(img_url, filepath):
                    local_images.append(f"/images/{provider}/{safe_id}/images/{filename}")

            place['local_images'] = local_images

            dbc.execute('''
                INSERT OR REPLACE INTO cafes (id, provider, provider_id, name, lat, lon, address, url, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (global_id, provider, provider_id, name, p_lat, p_lon, address.strip(), cafe_url, json.dumps(place)))

            with open(os.path.join(cafe_dir, 'cafe.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'id': global_id, 'provider': provider, 'provider_id': provider_id,
                    'name': name, 'lat': p_lat, 'lon': p_lon,
                    'address': address.strip(), 'url': cafe_url, 'metadata': place
                }, f, ensure_ascii=False, indent=2)

            print(f"Exported Naver cafe: {name} ({global_id})")

        dbc.execute('''
            INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
            VALUES (?, ?, ?, ?)
        ''', (grid_x, grid_y, provider, 'completed'))

        page.close()
        time.sleep(random.uniform(2.0, 4.0))
        return True

    except Exception as e:
        print(f"Error scraping grid ({grid_x}, {grid_y}): {e}")
        try:
            page.remove_listener("response", handle_response)
            page.close()
        except:
            pass
        return False

def main():
    parser = argparse.ArgumentParser(description="Naver Cafe Scraper")
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--start-step", type=int, default=0)
    args = parser.parse_args()

    init_db()
    dbc = DBClient()
    coords = get_spiral_coordinates(args.max_steps)

    if args.max_steps >= 2000 and check_if_done(dbc, 'naver', coords):
        print("All blocks within 20km radius are completed! Shutting down.")
        dbc.execute("INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status) VALUES (?, ?, ?, ?)",
                    (9999, 9999, 'naver_finished', 'completed'))
        import sys; sys.exit(42)

    coords_to_process = coords[args.start_step:]
    pass # print(f"Processing {len(coords_to_process)} grids starting from step {args.start_step}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
        except Exception as e:
            print(f"Failed to start browser: {e}")
            return

        for idx, (x, y) in enumerate(coords_to_process):
            if _shutdown.is_set():
                print("Shutdown requested — exiting loop.", flush=True)
                break

            current_step = args.start_step + idx
            pass # print(f"--- Step {current_step}/{args.max_steps} ---")

            success = False
            retries = 2
            while not success and retries > 0:
                timer = threading.Timer(300, watchdog, args=[300])
                timer.start()
                try:
                    success = scrape_naver_grid(context, dbc, x, y, CENTER_LAT + (y * 0.01), CENTER_LON + (x * 0.01))
                finally:
                    timer.cancel()

                if not success:
                    retries -= 1
                    print(f"Retrying... ({retries} left)")
                    time.sleep(5)

        browser.close()

    dbc.close()
    pass # print("Scraping iteration complete.")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
