# Checkpoint 00 — Region config + first Busan sample (2026-06-23)

## Did
- `data/regions.json`: active=[seoul,busan], origin=Seoul CityHall, per-region center+radius.
- `utils.py`: load config, `WORKCAFE_REGION` env, `region_grid_offset()`. Seoul=global origin; spiral shifted per region. Fallback defaults if file missing.
- Busan grid band offset (+204,-239) → disjoint from Seoul ±20 → no progress-key clash, one scraped.db.
- `Justfile`: `scrape-one` gains region arg.

## Verified
- seoul offset (0,0), busan (204,-239). spiral[0] correct. bad region raises. missing-file falls back. ✓
- Fired `just scrape-one kakao 9 busan` → 90 cafes, coords lat[35.085,35.117] lon[129.018,129.045] (on target), no dup names. ✓
- FINDING: name has raw `&amp;` (scraper not unescaping) → refine later.

## Note
- Long scrape = background+poll, don't block (cells slow via Tor). 7/9 cells in 300s = not stuck.
