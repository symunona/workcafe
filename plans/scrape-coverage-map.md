# Spinoff Plan — Scrape Coverage Map Overlay

> Visualize WHAT got scraped, WHERE, per provider/region — "snail style" (the spiral shows itself).
> Read-only view. No "mark area" UX yet (that comes later; regions added manually).

## Goal
A toggle-able map layer (under **Layers**) that draws the 1 km grid cells we've touched,
as rectangles, colored by coverage, with each provider's cafe-count for that cell/region.

## Data (already exists)
- `progress(grid_x, grid_y, provider, status)` — which cells each provider finished.
- `scraped_cafes(lat, lon, provider)` — count per cell = `floor((lon-CENTER_LON)/0.01)`, etc.
- Grid → bbox via `utils.get_bounding_box(grid_x, grid_y)` (already implemented).

## API — new endpoint `/api/scrape-coverage`
Query: optional `?region=busan` (or bbox). Returns, per cell:
```json
{ "grid_x":204,"grid_y":-239,
  "bbox":[minLat,minLon,maxLat,maxLon],
  "providers":{"kakao":{"status":"completed","cafes":37},"google":{...}},
  "total_cafes":37 }
```
- Cell list = union of progress rows + cells containing scraped_cafes.
- Cheap: one GROUP BY over progress + one over scraped_cafes binned to cells.
- Cache like /api/status (8s TTL).

## Frontend — Leaflet rectangle layer
- New layer under **Layers** toggle: "Scrape coverage".
- For each cell: `L.rectangle(bbox)` with:
  - **fill color** = heat by `total_cafes` (none→grey, low→pale, high→saturated).
  - **border** = solid rect (your "rect borders").
  - **corner labels** = each provider's count at a corner (kakao TL, google TR, naver BL, osm BR)
    — small divIcon, shown only at zoom ≥ 14 to avoid clutter.
- Per-region rollup chip: total cells done / total cafes per provider (in the status popout).
- The spiral scrape order makes the "snail" pattern visible automatically.

## Where it lives
- Part of the **scrape status** view (the SettingsModal funnel), as a **separate popout**:
  a "Coverage" tab/button that flips the map into coverage mode + shows per-region counts.

## Build steps + checks
1. API endpoint → curl `/api/scrape-coverage?region=busan`, verify cell count == progress rows,
   per-provider cafe counts == direct SQL.
2. Frontend layer → `just build`, browser: toggle layer over Busan, see colored squares +
   corner counts, spiral shape visible. Screenshot.
3. Per-region rollup chip matches `/api/scrape-coverage` totals.

## Out of scope (later)
- Click-drag to mark a new region (the "mark an area" UX).
- Live auto-refresh while scraping (poll every Ns) — nice-to-have v2.
