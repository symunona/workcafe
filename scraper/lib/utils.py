import os
import re
import json
import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DB_SOCKET_PATH = '/tmp/workcafe_db.sock'
DB_PID_FILE    = '/tmp/workcafe_db.pid'


def normalize_provider_id(provider_id: str) -> str:
    """Return a filesystem-safe version of a provider_id (latin chars and digits only).

    The original provider_id is preserved in the DB; this normalized form is used
    exclusively for directory names and URL path segments so that paths never contain
    special characters that cause encoding or filesystem issues.

    Examples:
        '1371876716'  -> '1371876716'   (Naver/Kakao numeric IDs are unchanged)
        '!4m7!3m6!1s0x357ca...:0xb2...!8m' -> '4m7_3m6_1s0x357ca_0xb2_8m'
    """
    normalized = re.sub(r'[^a-zA-Z0-9]+', '_', provider_id).strip('_')
    return normalized[:120]

# ── Paths ────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
DATA_DIR = os.path.join(_PROJECT_ROOT, 'data', 'seoul')
DB_PATH = os.path.join(_PROJECT_ROOT, 'data', 'seoul', 'scraped.db')
REGIONS_FILE = os.path.join(_PROJECT_ROOT, 'data', 'regions.json')

# ── Regions ──────────────────────────────────────────────────────────────────
# Global grid origin: all grid_x/grid_y are integer offsets from this point, so
# every region shares ONE coordinate system and ONE scraped.db. Each region's
# spiral is shifted onto a disjoint band of the grid — Busan sits ~204 east /
# -239 south of Seoul, far outside Seoul's ±20 radius — so progress keys
# (grid_x, grid_y, provider) never collide between regions.
#
# Config: data/regions.json
#   { "active": ["seoul","busan"],
#     "origin":  {"lat":37.49023,"lon":126.994312},
#     "regions": {"seoul":{"lat":..,"lon":..,"radius_km":20}, ...} }
# The defaults below keep scrapers working if the file is missing or partial.
STEP_SIZE = 0.01  # ~1km per grid cell

_DEFAULT_REGION_CONFIG = {
    "active": ["seoul"],
    "origin": {"lat": 37.490230, "lon": 126.994312},
    "regions": {
        "seoul": {"lat": 37.490230, "lon": 126.994312, "radius_km": 20},
        "busan": {"lat": 35.10066,  "lon": 129.03185,  "radius_km": 20},
    },
}

def _load_region_config():
    try:
        with open(REGIONS_FILE) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_REGION_CONFIG)
    # Backfill missing keys so a partial file never crashes a scraper.
    cfg.setdefault("origin", _DEFAULT_REGION_CONFIG["origin"])
    cfg.setdefault("regions", _DEFAULT_REGION_CONFIG["regions"])
    cfg.setdefault("active", list(cfg["regions"].keys()))
    return cfg

REGION_CONFIG = _load_region_config()
_ORIGIN = REGION_CONFIG["origin"]
CENTER_LAT = _ORIGIN["lat"]   # global grid origin (kept as CENTER_* for back-compat)
CENTER_LON = _ORIGIN["lon"]

REGIONS = {name: (r["lat"], r["lon"]) for name, r in REGION_CONFIG["regions"].items()}
ACTIVE_REGIONS = [r for r in REGION_CONFIG["active"] if r in REGIONS]

# Region this process scrapes. WORKCAFE_REGION overrides for one-off runs;
# otherwise defaults to the first active region.
REGION = os.environ.get('WORKCAFE_REGION', (ACTIVE_REGIONS or ['seoul'])[0]).strip().lower()
if REGION not in REGIONS:
    raise ValueError(f"Unknown WORKCAFE_REGION={REGION!r}; known: {sorted(REGIONS)}")

def region_grid_offset(region=None):
    """(x, y) base offset of a region's spiral center, in grid cells from the origin."""
    lat, lon = REGIONS[region or REGION]
    return (round((lon - CENTER_LON) / STEP_SIZE),   # x ← longitude
            round((lat - CENTER_LAT) / STEP_SIZE))   # y ← latitude

def region_radius_km(region=None):
    return REGION_CONFIG["regions"][region or REGION].get("radius_km", 20)

# (x, y) offset for THIS process's region — consumed by get_spiral_coordinates.
REGION_GRID_OFFSET = region_grid_offset(REGION)

def get_tor_session():
    session = requests.Session()
    # Tor proxy
    session.proxies = {
        'http': 'socks5h://127.0.0.1:9050',
        'https': 'socks5h://127.0.0.1:9050'
    }
    # Retry strategy
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[ 429, 500, 502, 503, 504 ])
    session.mount('http://', HTTPAdapter(max_retries=retries))
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def get_db_conn(path=DB_PATH):
    """Open a DB connection with WAL mode and a 30s busy timeout.
    WAL allows concurrent readers + one writer; busy_timeout queues writers
    instead of immediately raising 'database is locked'."""
    conn = sqlite3.connect(path, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn

def init_tables(conn):
    """Create all tables. Called by db_server on startup."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS scraped_cafes (
            id TEXT PRIMARY KEY,
            provider TEXT,
            provider_id TEXT,
            name TEXT,
            lat REAL,
            lon REAL,
            address TEXT,
            url TEXT,
            metadata TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS progress (
            grid_x INTEGER,
            grid_y INTEGER,
            provider TEXT,
            status TEXT,
            PRIMARY KEY (grid_x, grid_y, provider)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cafe_id     TEXT,
            provider    TEXT,
            local_path  TEXT,
            image_url   TEXT,
            gallery_url TEXT,
            photo_id    TEXT,
            photo_type  TEXT,
            tags        TEXT,
            registered_at TEXT,
            width       INTEGER,
            height      INTEGER,
            file_size   INTEGER,
            exif_date   TEXT,
            exif_lat    REAL,
            exif_lon    REAL,
            UNIQUE(cafe_id, photo_id)
        )
    ''')
    conn.commit()


def init_db():
    """Create provider dirs. Tables are created by db_server on startup."""
    os.makedirs(DATA_DIR, exist_ok=True)
    for provider in ['osm', 'google', 'kakao', 'naver']:
        os.makedirs(os.path.join(DATA_DIR, provider), exist_ok=True)

def check_if_done(dbc, providers, coords):
    """
    Check all coords are 'completed' for all providers.
    providers: str or list of str.
    dbc: DBClient instance.
    """
    if not coords:
        return False

    if isinstance(providers, str):
        providers = [providers]

    for provider in providers:
        rows = dbc.fetchall(
            "SELECT grid_x, grid_y FROM progress WHERE provider=? AND status='completed'",
            (provider,)
        )
        completed = set(tuple(r) for r in rows)
        for x, y in coords:
            if (x, y) not in completed:
                return False

    return True

def get_spiral_coordinates(max_steps=100, max_radius_km=20):
    """
    Generates (x, y) coordinates in a spiral from (0, 0)
    Returns a list of coordinates so we can slice it if needed.
    """
    base_x, base_y = REGION_GRID_OFFSET
    coords = []
    x = 0
    y = 0
    dx = 0
    dy = -1
    for _ in range(max_steps):
        dist_km = ( (x * 0.88)**2 + (y * 1.11)**2 ) ** 0.5
        if dist_km <= max_radius_km:
            if (-max_steps/2 < x <= max_steps/2) and (-max_steps/2 < y <= max_steps/2):
                # Shift the local spiral onto this region's global grid band.
                coords.append((x + base_x, y + base_y))
        if x == y or (x < 0 and x == -y) or (x > 0 and x == 1-y):
            dx, dy = -dy, dx
        x, y = x + dx, y + dy
    return coords

def get_bounding_box(grid_x, grid_y):
    min_lat = CENTER_LAT + (grid_y - 0.5) * STEP_SIZE
    max_lat = CENTER_LAT + (grid_y + 0.5) * STEP_SIZE
    min_lon = CENTER_LON + (grid_x - 0.5) * STEP_SIZE
    max_lon = CENTER_LON + (grid_x + 0.5) * STEP_SIZE
    return min_lat, min_lon, max_lat, max_lon

