import os
import json
import sqlite3
import requests
import argparse
import time
import logging
import threading
from urllib.parse import urlparse, quote
from playwright.sync_api import sync_playwright
from utils import DB_PATH, DATA_DIR, get_tor_session, get_db_conn, db_execute, flush_db_queue
from disk_check import check_disk_limit, DiskLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper_images.log"),
        logging.StreamHandler()
    ]
)

def download_image(session, url, save_path):
    try:
        start_time = time.time()
        response = session.get(url, stream=True, timeout=15)
        response.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if time.time() - start_time > 60:
                    raise Exception("Download timed out (took more than 60 seconds)")
                f.write(chunk)
        return True
    except Exception as e:
        logging.error(f"Failed to download {url}: {e}")
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except:
                pass
        return False

def scrape_images_for_provider(provider):
    logging.info(f"Starting image scraping for provider: {provider}")
    
    conn = get_db_conn()
    cursor = conn.cursor()
    
    # We fetch all cafes for this provider where we have metadata stored
    cursor.execute('SELECT id, provider_id, metadata FROM cafes WHERE provider = ?', (provider,))
    rows = cursor.fetchall()
    
    session = get_tor_session()
    
    for cafe_id, provider_id, metadata_json in rows:
        try:
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                continue
                
            images_dir = os.path.join(DATA_DIR, provider, provider_id, 'images')
            
            # Determine image URLs based on provider schema
            image_urls = []
            
            if provider == 'naver':
                # Naver often has thumUrl or thumUrls in its metadata
                if 'thumUrl' in metadata and metadata['thumUrl']:
                    image_urls.append(metadata['thumUrl'])
                if 'thumUrls' in metadata and isinstance(metadata['thumUrls'], list):
                    image_urls.extend(metadata['thumUrls'])
                    
            elif provider == 'osm':
                # OSM might have image tags or wikimedia commons links
                tags = metadata.get('tags', {})
                if 'image' in tags:
                    image_urls.append(tags['image'])
                # Sometimes they link to wikidata/wikipedia which needs separate resolution
                
            elif provider == 'kakao':
                # Use image_info.image_main_urls for multiple images
                image_info = metadata.get('image_info', {})
                if image_info and isinstance(image_info.get('image_main_urls'), list):
                    image_urls.extend(image_info['image_main_urls'])
                elif metadata.get('img'):
                    image_urls.append(metadata['img'])
                    
                # If we have less than 2 images, try fetching more from the detail page
                if len(image_urls) < 2:
                    try:
                        cafe_url = f"https://place.map.kakao.com/{provider_id}"
                        logging.info(f"Fetching more photos for Kakao cafe {provider_id} from {cafe_url}")
                        with sync_playwright() as p:
                            browser = p.chromium.launch(headless=True)
                            page = browser.new_page()
                            page.goto(cafe_url, timeout=15000)
                            page.wait_for_timeout(3000)
                            
                            img_urls = page.evaluate('''() => {
                                const imgs = Array.from(document.querySelectorAll('.photo_area img, .list_photo img, .link_photo img'));
                                return imgs.map(img => img.src || img.getAttribute('data-src')).filter(Boolean);
                            }''')
                            
                            import urllib.parse
                            for img_url in img_urls:
                                orig_url = img_url
                                if 'cthumb' in img_url and 'fname=' in img_url:
                                    try:
                                        parsed = urllib.parse.urlparse(img_url)
                                        query = urllib.parse.parse_qs(parsed.query)
                                        if 'fname' in query:
                                            orig_url = urllib.parse.unquote(query['fname'][0])
                                    except:
                                        pass
                                if orig_url not in image_urls:
                                    image_urls.append(orig_url)
                                    
                                if len(image_urls) >= 15:  # Limit to 15 images
                                    break
                                    
                            browser.close()
                    except Exception as e:
                        logging.error(f"Error fetching Kakao photos for {provider_id}: {e}")

            elif provider == 'google':
                api_key = os.environ.get('GOOGLE_API_KEY')
                if not api_key:
                    logging.warning("GOOGLE_API_KEY not set, skipping Google images")
                    continue

                p_name = metadata.get('name', '')
                p_lat = metadata.get('lat')
                p_lon = metadata.get('lon')
                if not p_name or p_lat is None or p_lon is None:
                    continue

                # Cache photo references so we don't hit the API on every run
                refs_cache_path = os.path.join(DATA_DIR, provider, provider_id, 'photo_refs.json')
                os.makedirs(os.path.dirname(refs_cache_path), exist_ok=True)
                api_session = requests.Session()

                if os.path.exists(refs_cache_path):
                    with open(refs_cache_path) as f:
                        photo_refs = json.load(f)
                else:
                    photo_refs = []
                    try:
                        find_resp = api_session.get(
                            'https://maps.googleapis.com/maps/api/place/findplacefromtext/json',
                            params={
                                'input': p_name,
                                'inputtype': 'textquery',
                                'fields': 'place_id',
                                'locationbias': f'point:{p_lat},{p_lon}',
                                'key': api_key,
                            },
                            timeout=10
                        )
                        find_resp.raise_for_status()
                        candidates = find_resp.json().get('candidates', [])
                        if candidates:
                            place_id = candidates[0]['place_id']
                            details_resp = api_session.get(
                                'https://maps.googleapis.com/maps/api/place/details/json',
                                params={
                                    'place_id': place_id,
                                    'fields': 'photos',
                                    'key': api_key,
                                },
                                timeout=10
                            )
                            details_resp.raise_for_status()
                            photos = details_resp.json().get('result', {}).get('photos', [])
                            photo_refs = [p['photo_reference'] for p in photos[:10]]
                    except Exception as e:
                        logging.error(f"Places API error for {p_name}: {e}")

                    with open(refs_cache_path, 'w') as f:
                        json.dump(photo_refs, f)

                if not photo_refs:
                    continue

                os.makedirs(images_dir, exist_ok=True)
                local_paths = []
                for idx, ref in enumerate(photo_refs):
                    filename = f'img_{idx}.jpg'
                    save_path = os.path.join(images_dir, filename)
                    if not os.path.exists(save_path):
                        try:
                            check_disk_limit()
                        except DiskLimitExceeded as e:
                            logging.warning(str(e))
                            return
                        photo_url = (
                            f"https://maps.googleapis.com/maps/api/place/photo"
                            f"?maxwidth=800&photo_reference={ref}&key={api_key}"
                        )
                        logging.debug(f"Downloading Google photo {idx} for {p_name}")
                        if download_image(api_session, photo_url, save_path):
                            time.sleep(0.5)
                    if os.path.exists(save_path):
                        encoded_id = quote(provider_id, safe='')
                        local_paths.append(f'/images/google/{encoded_id}/images/{filename}')

                if local_paths:
                    metadata['local_images'] = local_paths
                    db_execute(conn, 
                        'UPDATE cafes SET metadata = ? WHERE id = ?',
                        (json.dumps(metadata, ensure_ascii=False), cafe_id)
                    )
                    logging.info(f"Saved {len(local_paths)} Google photos for {p_name}")
                continue  # skip the generic URL download loop below

            # Deduplicate while preserving order
            seen = set()
            deduped = []
            for u in image_urls:
                if u and u not in seen:
                    seen.add(u)
                    deduped.append(u)
            image_urls = deduped

            if not image_urls:
                continue

            os.makedirs(images_dir, exist_ok=True)
            logging.info(f"Found {len(image_urls)} images for cafe {cafe_id}")

            downloaded_files = []
            for idx, url in enumerate(image_urls):
                parsed_url = urlparse(url)
                ext = os.path.splitext(parsed_url.path)[1]
                if not ext or ext.lower() not in ['.jpg', '.jpeg', '.png', '.webp']:
                    ext = '.jpg'

                filename = f"img_{idx}{ext}"
                save_path = os.path.join(images_dir, filename)

                if not os.path.exists(save_path):
                    try:
                        check_disk_limit()
                    except DiskLimitExceeded as e:
                        logging.warning(str(e))
                        return
                    logging.debug(f"Downloading {url} -> {filename}")
                    if download_image(session, url, save_path):
                        time.sleep(1)

                if os.path.exists(save_path):
                    downloaded_files.append(filename)

            if downloaded_files:
                encoded_id = quote(provider_id, safe='')
                local_paths = [f'/images/{provider}/{encoded_id}/images/{f}' for f in downloaded_files]
                metadata['local_images'] = local_paths
                db_execute(conn, 
                    'UPDATE cafes SET metadata = ? WHERE id = ?',
                    (json.dumps(metadata, ensure_ascii=False), cafe_id)
                )
                logging.info(f"Saved {len(local_paths)} local image paths for cafe {cafe_id}")
        except Exception as e:
            logging.error(f"Unexpected error processing images for cafe {cafe_id}: {e}", exc_info=True)
                
    flush_db_queue(conn)
    conn.close()
    logging.info(f"Finished image scraping for provider: {provider}")

def main():
    parser = argparse.ArgumentParser(description="Cafe Image Scraper")
    parser.add_argument("--provider", type=str, choices=['naver', 'osm', 'google', 'kakao', 'all'], default='all', help="Provider to scrape images for")
    args = parser.parse_args()

    providers_to_scrape = ['naver', 'osm', 'google', 'kakao'] if args.provider == 'all' else [args.provider]

    for provider in providers_to_scrape:
        scrape_images_for_provider(provider)

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()