# Scraper AGENTS

## Directory Layout

```
scraper/
├── db_server.py         ← service: SQLite socket proxy (root — no PYTHONPATH needed)
├── ralph_loop.py        ← process runner: spawns all place scrapers, loops, os.chdir() to own dir
├── watchdog.py          ← service: image scraper watchdog (auto-restart on silence)
├── check_tor.py         ← Tor connectivity probe (called by Justfile tor-check recipe)
├── register_watchdog.sh ← helper: install/remove systemd watchdog timer
├── images/              ← image scrapers (Playwright + Chromium, heavy)
├── places/              ← place + metadata scrapers
├── lib/                 ← shared Python modules (on PYTHONPATH for all scrapers)
├── archive/             ← superseded versions — do not import or run
├── tools/               ← one-time ran scripts; each must have "# Run once: YYYY-MM-DD. Purpose: ..." header
├── tests/               ← ad-hoc manual test scripts
└── log/                 ← runtime logs (gitignored)
```

## Active Scrapers

| Script | Service | What it does |
|--------|---------|-------------|
| `places/scraper_kakao_v2.py` | `workcafe-scraper-kakao` | Kakao Maps API: cafe POIs for Seoul grid cells |
| `places/scraper_google_v2.py` | `workcafe-scraper-google` | Google Maps Places API: cafe POIs |
| `places/scraper_naver.py` | `workcafe-scraper-naver` | Naver Maps API: cafe POIs |
| `places/scraper_osm.py` | `workcafe-scraper-osm` | OpenStreetMap Overpass: cafe nodes |
| `places/scraper_kakao_metadata_v1.py` | `workcafe-kakao-metadata` | Kakao: website/phone/hours enrichment |
| `places/scraper_naver_metadata_v1.py` | `workcafe-naver-metadata` | Naver: website/phone/hours enrichment |
| `images/scraper_kakao_images_v3.py` | `workcafe-kakao-images` | Kakao: photo download + JPEG compress |
| `images/scraper_naver_images_v1.py` | `workcafe-naver-images` | Naver: photo download via Playwright |
| `images/scraper_google_images_v1.py` | `workcafe-google-images` | Google Maps: photo download via Playwright |

## Shared Library (`lib/`)

All scraper systemd units set `Environment="PYTHONPATH=$WDIR/scraper/lib"` — scripts in `images/` and `places/` can `import db_client`, `import utils` etc. without path hacks. ralph_loop.py subprocesses inherit PYTHONPATH.

| Module | Purpose |
|--------|---------|
| `db_client.py` | `DBClient`: connects to db_server via Unix socket, provides `execute()`, `fetchall()`, etc. |
| `utils.py` | `DB_PATH`, grid helpers, `SEOUL_BOUNDS`, tile/cell logic |
| `download_utils.py` | HTTP download with retry, timeout, User-Agent rotation |
| `image_utils.py` | JPEG compress to q75/1024px; used at scrape time |
| `disk_check.py` | `check_disk_space()` — stops image scrapers when disk < 2 GB |

## Data Flow

All scrapers write to `data/seoul/scraped.db` via `db_server` at `/tmp/workcafe_db.sock`. No scraper writes directly to the SQLite file — all writes go through the socket to prevent concurrent write conflicts.

```
Scraper → DBClient → Unix socket → db_server.py → scraped.db
```

## Output Schema (`scraped_cafes`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | Provider-specific ID (e.g. `kakao:123456`) |
| `name` | TEXT | Korean name as returned by provider |
| `lat`, `lon` | REAL | Coordinates |
| `address` | TEXT | Full address string |
| `provider` | TEXT | `kakao` / `google` / `naver` / `osm` |
| `scraped_at` | TIMESTAMP | When this row was inserted |
| `url` | TEXT | Provider URL to the cafe page |
| `belongs_to_cafe_id` | TEXT | FK → `clean_cafes.id` (set by normalize pipeline) |
| `name_embedding` | BLOB | 768-dim float32 embedding (set by normalize pipeline) |

Images are in `images` table: `(id, cafe_id, url, local_path, provider, scraped_at, belongs_to_cafe_id)`.

## Process Runner (`ralph_loop.py`)

Spawns all place scrapers as subprocesses, monitors them, and restarts on crash. Uses `os.chdir()` to its own directory before spawning — scrapers use relative paths for their config. Subprocesses inherit `PYTHONPATH` from the environment.

Start via `just start ralph` (not individual place scrapers — ralph manages them).

## Image Scraper Notes

Image scrapers use Playwright (Chromium). Known failure modes:

- **Silent hang**: body read can block forever if `AbortController` doesn't wrap both `fetch()` and `resp.text()`. Fixed in v3 (kakao) and v1 (naver). Check via `journalctl --user -u workcafe-kakao-images --since "1h ago"`.
- **SIGALRM NameError**: watchdog SIGALRM handler referenced wrong constant → swallowed by bare `except`. Fixed in kakao v3.
- **Browser stuck (not Python)**: if CPU is high with no log output, kill via `just restart <name>` — systemd kills the whole cgroup including Chromium children.

Watchdog (`watchdog.py`) auto-restarts image scrapers silent > 30 min. Check status at `/api/watchdog-status` or `data/watchdog-status.json`.

## Adding a New Scraper

1. Place script in `places/` or `images/` depending on type.
2. Use `DBClient` from `lib/db_client.py` — never open SQLite directly.
3. Add a systemd unit write in `Justfile` (copy existing pattern, include `Environment="PYTHONPATH=$WDIR/scraper/lib"`).
4. Add row to service table above.
5. If superseding an old version: move old file to `archive/` and update this table.
