# Checkpoint 03 — Merge ran, Busan LIVE end-to-end (2026-06-23 00:24)

## Did
- Ran merge in background (poll, not block) on 258 queue (90 busan kakao + 168 seoul google).
  Steps: start play-db → 00_sync → 05_englishify → 04_normalize → 06_link. Skipped global chains (not needed for busan to appear).

## Results
- 00_sync: +258 scraped_cafes, +730,555 images (clean.db caught up month-old image backlog too).
- 05_englishify: translated 217 new names via ollama qwen (~99% backlog cleared). ollama HTTP 200.
- 04_normalize: densest-first ran google(168) then kakao(90). google merged ~some into existing (all-provider-merge fix exercised). busan kakao → 90 NEW clean_cafes (no false merges, none nearby).
- 06_link: linked 730,338 images, 0 missing.
- busan clean_cafes = 90. ✓

## END-TO-END VERIFIED
- DB: busan clean_cafes have Korean + English names (Cafe 051 Chungmu-dong, Yeong Coffee Nambu...), correct coords, ["kakao"].
- API: `/api/clean_cafes?minLat=34.92&maxLat=35.28&minLon=128.81&maxLon=129.25` → HTTP 200, **90 cafes**, lat 35.096 lon 129.024, english_name + chain + image_count present.
- => frontend refresh over Busan = cafes pop up w/ correct data. **USER "DONE" MET.**

## Still
- `&amp;` HTML entity persists in Korean names (englishify cleans EN side only). Refine: unescape at scraper or normalize.
- merge ran manually; streaming daemon (`merge_daemon.py`) built but NOT enabled as systemd service yet.
- Frontend funnel not browser-verified (vite :5550 down in agent namespace); API funnel live-verified + tsc-ok.
