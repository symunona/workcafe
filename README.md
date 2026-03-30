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

## Features

- Multi-provider data aggregation (Naver, Google, OSM)
- Spiral grid search for comprehensive coverage
- Image downloading with Tor proxy rotation
- Resumable scraping with progress tracking
- Independent, unranked search results
