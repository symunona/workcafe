import os
import json
import time
import random
import argparse
import signal
import threading
from utils import init_db, get_tor_session, get_spiral_coordinates, get_bounding_box, DATA_DIR, normalize_provider_id, check_if_done
from db_client import DBClient

_shutdown = threading.Event()


def _sigterm(sig, frame):
    print("SIGTERM received — finishing current grid then exiting.", flush=True)
    _shutdown.set()


signal.signal(signal.SIGTERM, _sigterm)


def scrape_osm(session, dbc, grid_x, grid_y):
    provider = 'osm'
    row = dbc.fetchone('SELECT status FROM progress WHERE grid_x=? AND grid_y=? AND provider=?',
                       (grid_x, grid_y, provider))
    if row and row[0] == 'completed':
        print(f"Skipping OSM grid ({grid_x}, {grid_y}), already completed.")
        return True

    min_lat, min_lon, max_lat, max_lon = get_bounding_box(grid_x, grid_y)

    overpass_url = "http://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="cafe"]({min_lat},{min_lon},{max_lat},{max_lon});
      way["amenity"="cafe"]({min_lat},{min_lon},{max_lat},{max_lon});
      relation["amenity"="cafe"]({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out center;
    """

    print(f"Scraping OSM grid ({grid_x}, {grid_y}). Bounding box: {min_lat},{min_lon},{max_lat},{max_lon}")

    try:
        response = session.post(overpass_url, data={'data': query}, timeout=30)
        if response.status_code == 429:
            print("Rate limited by Overpass API. Sleeping...")
            time.sleep(10)
            return False

        response.raise_for_status()
        data = response.json()

        elements = data.get('elements', [])
        print(f"Found {len(elements)} cafes in this grid.")

        for el in elements:
            provider_id = str(el['id'])
            tags = el.get('tags', {})
            name = tags.get('name', tags.get('name:en', 'Unknown'))

            if el['type'] == 'node':
                lat = el['lat']
                lon = el['lon']
            else:
                lat = el.get('center', {}).get('lat')
                lon = el.get('center', {}).get('lon')

            if not lat or not lon:
                continue

            address = tags.get('addr:street', '') + ' ' + tags.get('addr:housenumber', '')
            url = tags.get('website', '')
            global_id = f"{provider}_{provider_id}"

            dbc.execute('''
                INSERT OR REPLACE INTO cafes (id, provider, provider_id, name, lat, lon, address, url, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (global_id, provider, provider_id, name, lat, lon, address.strip(), url, json.dumps(tags)))

            cafe_dir = os.path.join(DATA_DIR, provider, normalize_provider_id(provider_id))
            os.makedirs(cafe_dir, exist_ok=True)
            with open(os.path.join(cafe_dir, 'cafe.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'id': global_id, 'provider': provider, 'provider_id': provider_id,
                    'name': name, 'lat': lat, 'lon': lon,
                    'address': address.strip(), 'url': url, 'metadata': tags
                }, f, ensure_ascii=False, indent=2)

            print(f"Exported OSM cafe: {name} ({global_id})")

        dbc.execute('''
            INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status)
            VALUES (?, ?, ?, ?)
        ''', (grid_x, grid_y, provider, 'completed'))

        time.sleep(random.uniform(1.5, 3.5))
        return True

    except Exception as e:
        print(f"Error scraping grid ({grid_x}, {grid_y}): {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="OSM Cafe Scraper")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--start-step", type=int, default=0)
    args = parser.parse_args()

    init_db()
    dbc = DBClient()
    session = get_tor_session()

    coords = get_spiral_coordinates(args.max_steps)

    if args.max_steps >= 2000 and check_if_done(dbc, 'osm', coords):
        print("All blocks within 20km radius are completed! Shutting down.")
        dbc.execute("INSERT OR REPLACE INTO progress (grid_x, grid_y, provider, status) VALUES (?, ?, ?, ?)",
                    (9999, 9999, 'osm_finished', 'completed'))
        import sys; sys.exit(42)

    try:
        ip_info = session.get("https://check.torproject.org/api/ip").json()
        print(f"Using Tor IP: {ip_info['IP']}")
    except Exception as e:
        print(f"Failed to verify Tor connection: {e}")
        return

    coords_to_process = coords[args.start_step:]
    print(f"Processing {len(coords_to_process)} grids starting from step {args.start_step}")

    for idx, (x, y) in enumerate(coords_to_process):
        if _shutdown.is_set():
            print("Shutdown requested — exiting loop.", flush=True)
            break

        current_step = args.start_step + idx
        print(f"--- Step {current_step}/{args.max_steps} ---")
        success = False
        retries = 3
        while not success and retries > 0:
            success = scrape_osm(session, dbc, x, y)
            if not success:
                retries -= 1
                time.sleep(5)

    dbc.close()
    print("Scraping iteration complete.")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
