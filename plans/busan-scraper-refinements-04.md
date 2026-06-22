# Checkpoint 04 — Busan IMAGES live + browser-verified (2026-06-23 00:47)

## Why no images before (user asked)
- Place scraper (scraper_kakao_v2) = POIs only, NO photos.
- Photos = separate `kakao-images` scraper, driven by `kakao_scrape_state` table (seeds all kakao cafes pending, picks ORDER BY RANDOM). It was INACTIVE → busan had 0 images.
- How I checked: `SELECT COUNT FROM images JOIN clean_cafes ... busan bbox` = 0; `/api/clean_cafes` image_count=0.

## Did
- Ran `scraper_kakao_images_v3.py --cafe-id <id>` for 6 random busan cafes (background, 120s cap each). → 6 cafes, 590 photos on disk.
- LINK BUG found: image scraper sets images.belongs_to_cafe_id from scraped.db.scraped_cafes.belongs_to_cafe_id — but normalize writes that column in CLEAN.db only, so scraped.db stays NULL → images unlinked after sync. FIX: run `06_link` (joins clean.db scraped_cafes). merge_daemon already runs 06_link each cycle → streaming OK.
- Flow: image-scrape (scraped.db) → 00_sync → 06_link → clean.db linked → API image_count.
- New recipe `just scrape-region-images busan 6` = focused region image scrape (bbox → --cafe-id loop).

## Verified END-TO-END (browser!)
- agent-browser run LOCAL by disabling sandbox (sandbox blocks localhost connect; ALSO vite :5550 was down → started frontend service).
- Map over Busan → markers show. Clicked 고니스커피 → detail pane "Gonis Coffee, Images: 5" + 5 real photos render (storefront, interior, dessert, drinks). SCREENSHOT tmp/busan_PICS.png.
- 6 busan cafes / 590 imgs linked in clean.db. API serves them.
- **NEW SUCCESS (sample pics) MET.**

## Focus/prioritize busan ("db magic")
- Places: `just scrape-one kakao N busan`.
- Images focused: `just scrape-region-images busan N` (targeted --cafe-id, ignores global random queue).
- To bias the RUNNING kakao-images service to busan: temporarily `UPDATE kakao_scrape_state SET status='paused' WHERE cafe_id NOT IN (busan ids)` then run service, restore after. Invasive (42k rows) — recipe is cleaner.

## Search quirk (noted, not pipeline)
- Frontend search box returns 0 for busan cafes (indexes a preloaded seoul set). Map markers + detail work fine. Refine later.
