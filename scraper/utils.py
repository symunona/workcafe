import os
import re
import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


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

# Seoul Center (City Hall)
CENTER_LAT = 37.490230
CENTER_LON = 126.994312
STEP_SIZE = 0.01  # Roughly 1km

DB_PATH = '../data/seoul/cafedata.db'
DATA_DIR = '../data/seoul'

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

_save_queue = []

def db_execute(conn, query, params=()):
    """
    Pushes a query to the _save_queue. Attempts to execute and commit all queries in the queue.
    If the database is locked, it rolls back and leaves the queries in the queue for the next call.
    """
    global _save_queue
    _save_queue.append((query, params))
    
    cursor = conn.cursor()
    try:
        for q, p in _save_queue:
            cursor.execute(q, p)
        conn.commit()
        _save_queue.clear()
        return True
    except sqlite3.OperationalError as e:
        conn.rollback()
        if 'locked' in str(e).lower() or 'busy' in str(e).lower():
            # Gently handle DB lock by leaving queries in the queue
            return False
        else:
            raise
    except Exception:
        conn.rollback()
        raise

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    for provider in ['osm', 'google', 'kakao', 'naver', 'foursquare']:
        os.makedirs(os.path.join(DATA_DIR, provider), exist_ok=True)

    conn = get_db_conn()
    cursor = conn.cursor()
    # Table to store scraped cafes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cafes (
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
    # Table to track progress
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS progress (
            grid_x INTEGER,
            grid_y INTEGER,
            provider TEXT,
            status TEXT,
            PRIMARY KEY (grid_x, grid_y, provider)
        )
    ''')
    conn.commit()
    return conn

def get_spiral_coordinates(max_steps=100):
    """
    Generates (x, y) coordinates in a spiral from (0, 0)
    Returns a list of coordinates so we can slice it if needed.
    """
    coords = []
    x = 0
    y = 0
    dx = 0
    dy = -1
    for _ in range(max_steps):
        if (-max_steps/2 < x <= max_steps/2) and (-max_steps/2 < y <= max_steps/2):
            coords.append((x, y))
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

def flush_db_queue(conn):
    """
    Flushes any remaining queries in the queue.
    """
    global _save_queue
    if not _save_queue:
        return True
    
    cursor = conn.cursor()
    try:
        for q, p in _save_queue:
            cursor.execute(q, p)
        conn.commit()
        _save_queue.clear()
        return True
    except sqlite3.OperationalError as e:
        conn.rollback()
        if 'locked' in str(e).lower() or 'busy' in str(e).lower():
            return False
        else:
            raise
    except Exception:
        conn.rollback()
        raise
