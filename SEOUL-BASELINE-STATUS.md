# Seoul Baseline — tag `seoul-baseline` @ f11b8a0

**Picked up:** 2026-06-21. This tag marks the codebase **before** the multi-region +
real-time-pipeline work began. Everything after commit `e96d547` is that work.

## What the codebase was at this tag
- **Single region.** One hardcoded scrape center (Seoul City Hall, `utils.py CENTER_LAT/LON`).
- Scrapers: kakao / google / naver / osm (places) + kakao/naver/google image downloaders.
- API (Go :13854) + Vite/React frontend (:5550). ~42k scraped cafes, ~29k clean.
- Pipeline = **manual** `just merge-pipeline` (sync → chains → englishify → normalize → link).
- Tagger = RAM++ (`tag_images_ram.py`, swin_large). A YOLO tagger also existed.
- Two-pass merge that **assumed kakao first** (insert-only anchors).

## What changed after (on `main`, from e96d547)
- Multi-region via `data/regions.json` (busan, haeundae); grid-offset so regions share one DB.
- Merge made **order-independent** (all providers spatial-merge densest-first; kakao no longer special).
- `merge_daemon.py` (continuous merge), `clean_region.py` (region-scoped wipe), status funnel.
- Moving toward a centralized always-on pipeline (translate → merge → image → tag watchers).

## Removed as obsolete (recover from this tag / git history)
Deleted to keep the tree simple — all recoverable at `git checkout seoul-baseline`:
- `scraper/archive/` — superseded scraper versions (scrape.py, scraper_google*, scraper_kakao*,
  scraper_images.py, scraper_kakao_images_v1/v2.py, scrape_v2.py). Live last at this tag.
- `scripts/tag_images_yolo.py` + the `tag-images-yolo` Justfile recipe — YOLO tagger,
  superseded by RAM++ (`tag_images_ram.py`).

## Kept as fallback (not obsolete)
- `just merge-pipeline` — manual one-shot merge; the basis for `merge_daemon.py`. Keep for debugging.
- `subset` / `test-merge-naebang` fixtures — used to test the merger.
