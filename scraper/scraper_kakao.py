import os
import json
import time
import random
import argparse
import pyproj
import threading
from playwright.sync_api import sync_playwright
from utils import init_db, get_spiral_coordinates, DATA_DIR, CENTER_LAT, CENTER_LON, normalize_provider_id, db_execute, flush_db_queue
from download_utils import download_image

def watchdog(timeout):
    print(f"Watchdog triggered! Process hung for more than {timeout} seconds. Exiting.")
    os._exit(1)

def scrape_kakao_grid(browser, conn, grid_x, grid_y, lat, lon):
    provider = 'kakao'
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?', (grid_x, grid_y, provider))
    row = cursor.fetchone()
    if row and row[0] == 'completed':
        print(f"Skipping Kakao grid ({grid_x}, {grid_y}), already completed.")
        return True

    context = browser.new_context(
        user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
        viewport={"width": 360, "height": 800}
    )
    page = context.new_page()

    api_data = []
    
    def handle_response(response):
        if "searchJson" in response.url:
            try:
                data = response.json()
                api_data.append(data)
            except Exception:
                pass

    page.on("response", handle_response)
    
    print(f"Scraping Kakao grid ({grid_x}, {grid_y}) at {lat}, {lon}")
    
    try:
        wgs84 = pyproj.CRS("EPSG:4326")
        wcongnamul = pyproj.CRS("EPSG:5181")
        transformer = pyproj.Transformer.from_crs(wgs84, wcongnamul, always_xy=True)
        x, y = transformer.transform(lon, lat)
        urlX = int(x * 2.5)
        urlY = int(y * 2.5)
        
        url = f"https://m.map.kakao.com/actions/searchView?q=%EC%B9%B4%ED%8E%98&wx={urlX}&wy={urlY}&level=4"
        
        try:
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"Goto timeout or error: {e}")
        page.wait_for_timeout(3000)
        
        import re
        html = page.content()
        
        places_found = []
        
        html_places = re.findall(r'<li class="search_item base" data-id="(\d+)".*?data-wx="(\d+)".*?data-wy="(\d+)".*?data-title="([^"]+)"', html)
        for pid, pwx, pwy, ptitle in html_places:
            places_found.append({
                'id': pid,
                'name': ptitle,
                'x': int(pwx),
                'y': int(pwy)
            })
            
        for _ in range(3):
            try:
                page.evaluate('document.querySelector(".link_more[data-type=\'place\']").click()')
                page.wait_for_timeout(2000)
            except Exception:
                break
                
        page.remove_listener("response", handle_response)
        
        for data in api_data:
            if 'placeList' in data:
                for place in data['placeList']:
                    places_found.append({
                        'id': place.get('confirmid', ''),
                        'name': place.get('name', ''),
                        'x': place.get('x', 0),
                        'y': place.get('y', 0),
                        'address': place.get('address', ''),
                        'metadata': place
                    })
                    
        unique_places = {}
        for p in places_found:
            if p['id'] and p['id'] not in unique_places:
                unique_places[p['id']] = p
                
        places = list(unique_places.values())
        
        print(f"Found {len(places)} cafes in this grid.")
        
        if not places:
            print("No cafes found.")
            db_execute(conn, '''
                INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
                VALUES (?, ?, ?, ?)
            ''', (grid_x, grid_y, provider, 'completed'))
            return True
            
        transformer_back = pyproj.Transformer.from_crs(wcongnamul, wgs84, always_xy=True)
        
        for place in places:
            provider_id = str(place['id'])
            name = place['name']
            
            p_x = place['x'] / 2.5
            p_y = place['y'] / 2.5
            
            p_lon, p_lat = transformer_back.transform(p_x, p_y)
            
            address = place.get('address', '')
            cafe_url = f"https://place.map.kakao.com/{provider_id}"
            global_id = f"{provider}_{provider_id}"
            
            db_execute(conn, '''
                INSERT OR REPLACE INTO cafes (id, provider, provider_id, name, lat, lon, address, url, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (global_id, provider, provider_id, name, p_lat, p_lon, address.strip(), cafe_url, json.dumps(place.get('metadata', {}))))
            
            safe_id = normalize_provider_id(provider_id)
            cafe_dir = os.path.join(DATA_DIR, provider, safe_id)
            os.makedirs(cafe_dir, exist_ok=True)

            # Extract and download images
            img_dir = os.path.join(cafe_dir, 'images')
            os.makedirs(img_dir, exist_ok=True)
            
            # The search API sometimes returns a main image
            main_img = place.get('img', '')
            if main_img:
                download_image(main_img, os.path.join(img_dir, 'main.jpg'))
                
            # Fetch additional photos by visiting the place detail page
            try:
                detail_page = context.new_page()
                detail_page.goto(cafe_url, timeout=15000)
                detail_page.wait_for_timeout(2000)
                
                img_urls = detail_page.evaluate('''() => {
                    const imgs = Array.from(document.querySelectorAll('.photo_area img, .list_photo img, .link_photo img'));
                    return imgs.map(img => img.src || img.getAttribute('data-src')).filter(Boolean);
                }''')
                
                import urllib.parse
                downloaded_count = 0
                for i, img_url in enumerate(img_urls):
                    if downloaded_count >= 15:  # Increased limit to 15 images per cafe
                        break
                        
                    orig_url = img_url
                    if 'cthumb' in img_url and 'fname=' in img_url:
                        try:
                            parsed = urllib.parse.urlparse(img_url)
                            query = urllib.parse.parse_qs(parsed.query)
                            if 'fname' in query:
                                orig_url = urllib.parse.unquote(query['fname'][0])
                        except:
                            pass
                            
                    if download_image(orig_url, os.path.join(img_dir, f'photo_{downloaded_count}.jpg')):
                        downloaded_count += 1

                detail_page.close()
            except Exception as e:
                print(f"Error fetching photos for {name}: {e}")
                try:
                    detail_page.close()
                except:
                    pass

            # Build local_images from whatever landed in img_dir and update the DB row
            IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
            downloaded_files = sorted(
                f for f in os.listdir(img_dir)
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
            )
            if downloaded_files:
                local_images = [f"/images/{provider}/{safe_id}/images/{f}" for f in downloaded_files]
                db_execute(conn, 
                    'UPDATE cafes SET metadata = json_set(COALESCE(metadata, "{}"), "$.local_images", json(?)) WHERE id = ?',
                    (json.dumps(local_images), global_id)
                )

            with open(os.path.join(cafe_dir, 'cafe.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'id': global_id,
                    'provider': provider,
                    'provider_id': provider_id,
                    'name': name,
                    'lat': p_lat,
                    'lon': p_lon,
                    'address': address.strip(),
                    'url': cafe_url,
                    'metadata': place.get('metadata', {})
                }, f, ensure_ascii=False, indent=2)
            
        print(f"Exported {len(places)} Kakao cafes.")
                
        db_execute(conn, '''
            INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
            VALUES (?, ?, ?, ?)
        ''', (grid_x, grid_y, provider, 'completed'))
        
        time.sleep(random.uniform(2.0, 4.0))
        try:
            page.close()
            context.close()
        except:
            pass
        return True
        
    except Exception as e:
        print(f"Error scraping grid ({grid_x}, {grid_y}): {e}")
        try:
            page.screenshot(path=f"error_{grid_x}_{grid_y}.png")
            page.remove_listener("response", handle_response)
            page.close()
            context.close()
        except:
            pass
        return False

def main():
    parser = argparse.ArgumentParser(description="Kakao Cafe Scraper")
    parser.add_argument("--max-steps", type=int, default=5, help="Total number of spiral steps to generate")
    parser.add_argument("--start-step", type=int, default=0, help="Step index to start from")
    args = parser.parse_args()

    conn = init_db()
    coords = get_spiral_coordinates(args.max_steps)
    coords_to_process = coords[args.start_step:]
    
    print(f"Processing {len(coords_to_process)} grids starting from step {args.start_step}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            
        except Exception as e:
            print(f"Failed to start browser: {e}")
            return

        for idx, (x, y) in enumerate(coords_to_process):
            current_step = args.start_step + idx
            print(f"--- Step {current_step}/{args.max_steps} ---")
            
            grid_lat = CENTER_LAT + (y * 0.01)
            grid_lon = CENTER_LON + (x * 0.01)
            
            success = False
            retries = 2
            while not success and retries > 0:
                timer = threading.Timer(300, watchdog, args=[300])
                timer.start()
                try:
                    success = scrape_kakao_grid(browser, conn, x, y, grid_lat, grid_lon)
                finally:
                    timer.cancel()
                    
                if not success:
                    retries -= 1
                    print(f"Retrying... ({retries} left)")
                    time.sleep(5)
                    
        browser.close()
                
    flush_db_queue(conn)
    conn.close()
    print("Scraping iteration complete.")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
