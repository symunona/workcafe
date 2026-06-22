# Checkpoint 01 — Safety (backup + cleaner) + push (2026-06-23)

## Did
- Backups `data/seoul/backups/pre-busan-2026-06-22/` via online `sqlite3 .backup` (scrapers stay live). README w/ restore steps.
- `clean_region.py`: delete ONE region by bbox; Seoul REFUSED; dry-run default; --confirm to delete. children-first (image_tags→images→clean_cafes→scraped_cafes→progress).
- `04_normalize`: kakao-first assumption KILLED. all providers spatial-merge; densest-first per run. same-provider guard stops self-collapse.
- `merge_daemon.py`: poll watermark (unsynced + belongs_to_cafe_id NULL), debounce, run incremental chain on play socket.
- Justfile: backup-dbs, clean-region, merge-daemon recipes.

## Verified
- Backup integrity ok, counts match (scraped 41958, clean_cafes 29231). ✓
- Cleaner wiped expendable busan (stray "sobo" google cafe + 8 imgs), Seoul intact. dry-run after = 0 busan rows. ✓
- normalize + utils compile. ✓

## Push
- commit e96d547 → origin/main. regions.json force-added (data/ gitignored).
