import os
import json
import time
import random
import argparse
import urllib.parse
import logging
import threading
from playwright.sync_api import sync_playwright
from utils import init_db, get_spiral_coordinates, DATA_DIR, CENTER_LAT, CENTER_LON, normalize_provider_id, db_execute, flush_db_queue
from download_utils import download_image

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("log/scraper_google.log"),
        logging.StreamHandler()
    ]
)

def watchdog(timeout):
    logging.error(f"Watchdog triggered! Process hung for more than {timeout} seconds. Exiting.")
    os._exit(1)

def scrape_google_grid(page, conn, grid_x, grid_y, lat, lon):
    provider = 'google'
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?', (grid_x, grid_y, provider))
    row = cursor.fetchone()
    if row and row[0] == 'completed':
        pass # logging.info(f"Skipping Google grid ({grid_x}, {grid_y}), already completed.")
        return True

    logging.info(f"Scraping Google grid ({grid_x}, {grid_y}) at {lat}, {lon}")
    
    # We will search for "cafe" at the specific coordinates
    # Google Maps URL format for searching near a coordinate:
    # https://www.google.com/maps/search/cafe/@LAT,LON,15z
    search_query = urllib.parse.quote("cafe")
    url = f"https://www.google.com/maps/search/{search_query}/@{lat},{lon},15z?hl=en"
    
    try:
        # Increase default timeout for the page
        page.set_default_timeout(60000)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        
        # Accept cookies if prompted (EU/some regions)
        try:
            # Check for cookie consent dialog which often blocks the page
            # We see it's a full-page Google consent screen in German or English
            # Wait a bit to let any redirects to consent page finish
            page.wait_for_timeout(3000)
            
            # The consent screen uses forms with submit buttons. Let's look for the text
            # specifically within a button or submit element.
            consent_texts = [
                "Accept all", "Reject all", 
                "Alle akzeptieren", "Alle ablehnen",
                "Godkänn alla", "Avvisa alla",
                "Tout accepter", "Tout refuser",
                "Aceptar todo", "Rechazar todo",
                "Accetta tutto", "Rifiuta tutto",
                "Alles accepteren", "Alles afwijzen"
            ]
            for text in consent_texts:
                try:
                    # Look for span containing the text, then click its parent button
                    # The screenshot shows blue buttons at the bottom.
                    btn = page.locator(f"button:has-text('{text}')")
                    if btn.count() > 0:
                        for i in range(btn.count()):
                            if btn.nth(i).is_visible():
                                btn.nth(i).click(timeout=3000)
                                page.wait_for_timeout(3000)
                                break
                except:
                    pass
            
            # Sometimes Google uses a different DOM structure for the consent screen
            # Let's try clicking the second button in the consent form (usually Accept All)
            if "consent.google.com" in page.url:
                try:
                    buttons = page.locator("form").first.locator("button")
                    if buttons.count() >= 2:
                        buttons.nth(1).click(timeout=3000)
                        page.wait_for_timeout(3000)
                    else:
                        buttons.last.click(timeout=3000)
                        page.wait_for_timeout(3000)
                except:
                    pass
        except:
            pass

        # Scroll the results pane to load more
        results_pane_selector = 'div[role="feed"]'
        
        try:
            page.wait_for_selector(results_pane_selector, timeout=10000)
        except:
            logging.warning(f"Could not find results pane for grid ({grid_x}, {grid_y}). Might be no results or layout changed.")
            page.screenshot(path=f"log/error_grid_{grid_x}_{grid_y}.png")
            return False

        # Scroll to load more results
        for _ in range(5):
            page.evaluate(f'''
                const pane = document.querySelector('{results_pane_selector}');
                if (pane) pane.scrollTop = pane.scrollHeight;
            ''')
            page.wait_for_timeout(1500)

        # Extract cafe data
        places = page.evaluate('''() => {
            const results = [];
            const items = document.querySelectorAll('a[href^="https://www.google.com/maps/place/"]');
            
            items.forEach(item => {
                const url = item.href;
                const nameMatch = url.match(/place\\/([^\\/]+)/);
                let name = nameMatch ? decodeURIComponent(nameMatch[1].replace(/\\+/g, ' ')) : 'Unknown';
                
                // Try to get aria-label which often contains the clean name
                const ariaLabel = item.getAttribute('aria-label');
                if (ariaLabel) {
                    name = ariaLabel;
                }

                // Extract coordinates from URL if present (usually in the format !3dLAT!4dLON)
                let lat = null;
                let lon = null;
                const coordMatch = url.match(/!3d([\\d\\.-]+)!4d([\\d\\.-]+)/);
                if (coordMatch) {
                    lat = parseFloat(coordMatch[1]);
                    lon = parseFloat(coordMatch[2]);
                }
                
                // We use a hash of the URL or the place name as ID since Google's exact ID is hidden in complex pb params
                const idMatch = url.match(/data=(.+?)(&|$)/);
                const dataParam = idMatch ? idMatch[1] : null;

                if (url && name) {
                    results.push({
                        url: url,
                        name: name,
                        lat: lat,
                        lon: lon,
                        provider_id: dataParam || encodeURIComponent(name)
                    });
                }
            });
            
            // Deduplicate by URL
            const unique = [];
            const seen = new Set();
            for (const item of results) {
                if (!seen.has(item.url)) {
                    seen.add(item.url);
                    unique.push(item);
                }
            }
            return unique;
        }''')

        logging.info(f"Found {len(places)} cafes in this grid.")
        
        for place in places:
            provider_id = place.get('provider_id')
            if not provider_id:
                continue
                
            name = place.get('name', 'Unknown')
            p_lat = place.get('lat')
            p_lon = place.get('lon')
            cafe_url = place.get('url')
            
            # Click into the detail page to get images
            # Click into the detail page to get images
            images = []
            try:
                detail_page = page.context.new_page()
                # To get directly to photos, we can append /photos to the url or just go to the URL and click the photos tab
                # Going directly to the URL is safer
                detail_page.goto(cafe_url, wait_until="domcontentloaded")
                detail_page.wait_for_timeout(3000)
                
                # Look for images in the detail page
                # Google Maps usually has buttons with images in the left pane
                images = detail_page.evaluate(r'''() => {
                    const imgElements = document.querySelectorAll('img');
                    const urls = [];
                    imgElements.forEach(img => {
                        let src = img.src;
                        if (src && src.includes('googleusercontent.com') && !src.includes('Avatar')) {
                            // Google uses wXXX-hXXX parameters to set size. Replace with w1024-h768 for high res
                            let highResSrc = src.replace(/=w\d+-h\d+/, '=w1024-h768');
                            if (!highResSrc.includes('=')) {
                                highResSrc += '=w1024-h768';
                            }
                            urls.push(highResSrc);
                        }
                    });
                    return Array.from(new Set(urls)).slice(0, 10);
                }''')
                
                detail_page.close()
            except Exception as e:
                logging.error(f"Error fetching detail page for {name}: {e}")
                try:
                    detail_page.close()
                except:
                    pass
            logging.info(f"Found {len(images)} images for {name}")

            # If we couldn't extract coordinates from URL, fallback to grid center (approximate)
            if p_lat is None or p_lon is None:
                p_lat = lat
                p_lon = lon

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
            
            db_execute(conn, '''
                INSERT OR REPLACE INTO cafes (id, provider, provider_id, name, lat, lon, address, url, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (global_id, provider, provider_id, name, p_lat, p_lon, '', cafe_url, json.dumps(place)))
            
            with open(os.path.join(cafe_dir, 'cafe.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'id': global_id,
                    'provider': provider,
                    'provider_id': provider_id,
                    'name': name,
                    'lat': p_lat,
                    'lon': p_lon,
                    'address': '',
                    'url': cafe_url,
                    'metadata': place
                }, f, ensure_ascii=False, indent=2)
            
            logging.debug(f"Exported Google cafe: {name} ({global_id})")
                
        db_execute(conn, '''
            INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
            VALUES (?, ?, ?, ?)
        ''', (grid_x, grid_y, provider, 'completed'))
        
        time.sleep(random.uniform(2.0, 4.0))
        return True
        
    except Exception as e:
        logging.error(f"Error scraping grid ({grid_x}, {grid_y}): {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Google Maps Cafe Scraper")
    parser.add_argument("--max-steps", type=int, default=5, help="Total number of spiral steps to generate")
    parser.add_argument("--start-step", type=int, default=0, help="Step index to start from")
    args = parser.parse_args()

    conn = init_db()
    coords = get_spiral_coordinates(args.max_steps)
    coords_to_process = coords[args.start_step:]
    
    pass # logging.info(f"Processing {len(coords_to_process)} grids starting from step {args.start_step}")

    with sync_playwright() as p:
        try:
            # Using Tor proxy if needed, or just standard connection
            # For Google, rotating IPs via Tor is highly recommended to avoid CAPTCHAs
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"} # Route through Tor
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US"
            )
            page = context.new_page()
            
        except Exception as e:
            logging.error(f"Failed to start browser (is Tor running on 9050?): {e}")
            logging.info("Falling back to direct connection...")
            try:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US"
                )
                page = context.new_page()
            except Exception as e2:
                logging.error(f"Failed to start browser directly: {e2}")
                return

        for idx, (x, y) in enumerate(coords_to_process):
            current_step = args.start_step + idx
            pass # logging.info(f"--- Step {current_step}/{args.max_steps} ---")
            
            grid_lat = CENTER_LAT + (y * 0.01)
            grid_lon = CENTER_LON + (x * 0.01)
            
            success = False
            retries = 2
            while not success and retries > 0:
                # 5 minutes watchdog per grid
                timer = threading.Timer(300, watchdog, args=[300])
                timer.start()
                try:
                    success = scrape_google_grid(page, conn, x, y, grid_lat, grid_lon)
                except Exception as e:
                    logging.error(f"Error in grid loop: {e}")
                    success = False
                finally:
                    timer.cancel()

                if not success:
                    retries -= 1
                    logging.info(f"Retrying... ({retries} left)")
                    time.sleep(5)
                    
        browser.close()
                
    flush_db_queue(conn)
    conn.close()
    pass # logging.info("Scraping iteration complete.")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()