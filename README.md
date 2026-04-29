# Work Cafe Map

Independent cafe search platform. Scrapes cafe data from multiple map providers, unifies results, and visualizes on an interactive map without platform bias.

Adding metadata via image classifications.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Scrapers   │────▶│    Data     │────▶│   Web App   │
│   (Python)  │     │  (SQLite)   │     │(Go + React) │
└─────────────┘     └─────────────┘     └─────────────┘
```

**Data Collection**: Python scrapers for Naver Maps, Google Maps, and OpenStreetMap using Playwright and Tor proxy for rate limit avoidance.

**Storage**: SQLite with progress tracking + JSON metadata files per cafe for raw provider data.

**Backend**: Go HTTP server (`:8090`) serving `/api/cafes` endpoint.

**Frontend**: Vite + React + Leaflet map (`:5550`), proxies API calls to backend.

## Project Structure

```
├── api/            # Go backend
├── frontend/       # React + Vite frontend
├── scraper/        # Python scrapers
│   ├── scraper_google.py
│   ├── scraper_naver.py
│   ├── scraper_osm.py
│   └── scraper_images.py
├── data/           # SQLite DB + scraped JSON files (gitignored)
└── .beads/         # Dolt database for issue tracking
```

## Quick Start

**Web Services (Systemd)**:
The web services are managed via user systemd units.
- **Backend API**: `workcafe-api`
- **Frontend App**: `workcafe-frontend`

You can restart them using:
```bash
systemctl --user restart workcafe-api workcafe-frontend
```

Alternatively, use the provided `Justfile`:
```bash
just service api restart
just service frontend restart
```

**Scraper** (Python 3):
```bash
cd scraper
python -m venv venv && source venv/bin/activate
pip install playwright requests
playwright install
python scraper_naver.py  # or scraper_google.py, scraper_osm.py
```

**API** (Go):
```bash
cd api
go run main.go  # Serves on :8090
```

**Frontend** (Node.js):
```bash
cd frontend
pnpm install
pnpm dev  # Serves on :5550
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `../data/seoul/cafedata.db` | SQLite database path |
| `DATA_DIR` | `../data/seoul` | Image assets directory |

## Image Processing Versions

Each tagger script has a `TAGGER` constant (e.g. `ram_plus_v2`). **Bump the version whenever you modify tag lists, scoring logic, or models** — this lets us identify which images need re-tagging after a change.

| Tagger | Current Version | What changed |
|--------|----------------|--------------|
| `tag_images_ram.py` | `ram_plus_v3` | Added food/drink/amenity/pet/ambiance tags; fixed eggplant→plant bug; scene tags |
| `tag_images_clip.py` | `clip_v1` | Initial version |
| `tag_images_yolo.py` | `yolo_oiv7_v1` | Initial version |

To find images tagged before a change (e.g. missing scene tags):
```sql
SELECT COUNT(DISTINCT image_id) FROM image_tags WHERE tagger = 'ram_plus_v1';
-- NULL tagger = tagged before version tracking was added
SELECT COUNT(DISTINCT image_id) FROM image_tags WHERE tagger IS NULL;
```

## Features

- Multi-provider data aggregation (Naver, Google, OSM)
- Spiral grid search for comprehensive coverage
- Image downloading with Tor proxy rotation
- Resumable scraping with progress tracking
- Independent, unranked search results
