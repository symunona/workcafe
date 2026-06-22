# Checkpoint 05 — Iteration 2: wider Busan + more pics (2026-06-23 01:34)

## Ran (background orchestrator tmp/busan_iterate.sh, ~24 min)
- STAGE 1 places: `WORKCAFE_REGION=busan scraper_kakao_v2 --max-steps 25` (resumed past done cells).
- STAGE 2 merge: sync (+158 cafes) → englishify → normalize (158 → clean_cafes) → 06_link.
- STAGE 3 images: 12 random busan cafes, 90s cap each.
- STAGE 4: sync + 06_link (+940 image links).

## Results (verified API + browser)
- busan clean_cafes: 90 → **248**.
- busan cafes_with_photos: 6 → **18** (1,239 photos).
- API `/api/clean_cafes` busan bbox = 248, 18 with image_count>0.
- Map screenshot tmp/busan_iter2.png: dense markers across Busan (~73 in view).
- Resumability proven: progress table skipped already-done cells.

## Loop tooling proven
- `just scrape-one kakao N busan` → places.
- merge chain (sync/englishify/normalize/link) → clean_cafes.
- `just scrape-region-images busan N` → focused photos.
- `clean_region.py --region busan --confirm` → wipe + redo, Seoul safe.
