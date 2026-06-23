# Unified Real-Time Pipeline — Build Plan

**Goal:** mark a region → minutes later cafes appear on the map (then photos), with **no manual steps**.
Reuse existing DBs + scripts; add continuous, config-driven watchers + status columns. **No DB rewrite.**

## Decisions (locked)
- Reuse `scraped.db` + `clean.db` + `englishify.db` (englishify stays a separate translator cache).
- **LLM on CPU** (qwen2.5:1.5b for translate + merge fallback); **RAM++ tagger on GPU**.
  Bench: CPU 0.52 names/s (150 names ~5 min); GPU only 12% faster (compute-bound by tagger). `plans/bench-cpu-translation.md`.
- **Order B: translate → merge.** Translation raises cross-language merge accuracy (merger test 4b vs 4c).
- **Config = `data/pipeline.json`** (read via `--config`; env only for dev override).
- **State = `status` column on `scraped_cafes`** (`scraped → translated → merged`). No queue table.
- **Insert dedup:** `INSERT OR IGNORE` by provider id (already true) → multi-region overlap never re-adds/overwrites.
- **Immutable scraped fields:** translation/derived live in additive columns (or englishify.db); raw scrape never updated. Re-listing a cafe later = ignored (edge case, accepted).
- **Chains:** on-the-fly assign + threshold-promote (≥`chain_promote_min`); fuzzy consolidation = manual recipe.
- **Image priority:** per-region baseline — a freshly-marked region's cafes get first ~30 imgs each, jumping the backlog.
- **Service control:** ONLY via `just scraper-start | scraper-stop | scraper-status` (see CLAUDE.md).

## DB changes — `01_migrate_db.py` (additive, idempotent)
- `scraped_cafes.status TEXT DEFAULT 'scraped'` — `scraped → translated → merged`. (Scraped fields untouched; this is additive.)
- `scraped_cafes.translated_at TEXT`.
- `clean_cafes.metadata` — already fixed.
- `kakao_scrape_state.priority INTEGER DEFAULT 0` — image queue baseline.
- New `merge_log(ts, scraped_id, clean_id, method, detail)` — `method ∈ {distance,name,chain,llm}`; every merge logged, LLM ones especially (debug visibility).
- Image stage is derived, no new column: downloaded = `images.file_size>0`; tagged = row in `image_tags`.

## Config — `data/pipeline.json`
```json
{
  "poll_interval_s": 30,
  "merge_debounce_s": 60,
  "translate_batch": 30,
  "image_priority_first_n": 30,
  "chain_promote_min": 5,
  "llm_on_cpu": true
}
```
Regions still come from `data/regions.json` (`active` list). Workers read both via `--config` / `--db`.

## Processes (each a systemd user service, config-driven, shown in `scraper-status`)
1. **scrapers** (existing, per provider) — iterate `active` regions → write `scraped_cafes(status='scraped')`.
2. **translate-watcher** (CPU) — poll names lacking `english_name` → qwen (CPU, `num_gpu:0`) → englishify.db; set `status='translated'`.
3. **merge-watcher** (evolve `merge_daemon.py`) — poll `status='translated'`, debounce → normalize (chain assign on-the-fly) → clean_cafes + link images → `status='merged'`; write `merge_log`.
4. **image-download pool** (existing image scrapers) — `priority` baseline; first ~30 imgs per new-region cafe.
5. **tagger** (existing RAM++ on GPU) — tag downloaded images.

## Command
`just scrape-and-process-full-pipeline [region=all]`
- `scraper-start` (ensure watchers up) → add region to `regions.json active` if given → kick scrapers → return.
- Watchers carry it. Prints the monitoring commands below.

## How to check DETAILED status of each process
- **Everything at a glance:** `just scraper-status`
- **A scraper:** `journalctl --user -u workcafe-scraper-naver -f` (swap kakao/google/osm)
- **Translate watcher:** `journalctl --user -u workcafe-translate -f`
- **Merge watcher:** `journalctl --user -u workcafe-merge -f`
- **Merge decisions / LLM merges:** `sqlite3 data/seoul/clean.db "SELECT ts,method,detail FROM merge_log ORDER BY ts DESC LIMIT 30;"` and `... WHERE method='llm'`
- **Tagger (GPU):** `tmux attach -t image-pipeline`; GPU: `nvidia-smi`
- **Queue depths:** `sqlite3 data/seoul/scraped.db "SELECT status,COUNT(*) FROM scraped_cafes GROUP BY status;"`
- **Funnel / per-region counts:** `/api/status` (and planned `/api/scrape-coverage`)
- **Image stage:** `sqlite3 ... "SELECT COUNT(*) downloaded ... file_size>0; ... tagged via image_tags;"`

## Test gate (must pass before/after changes)
- `python data-processing/tests/run_merger_tests.py` — 17 synthetic cases.
- `just test-merge-naebang` — real-data sanity (6/6).
- **Test run:** `scraper-stop` → migrate → `scraper-start` (watchers) → mark a small Busan area → watch cafes appear within minutes → verify multi-source `providers`, `english_name` filled, photos linked.

## Phasing
- **Phase 1 (now):** config + migrate (status, priority, merge_log) + translate-watcher + merge-watcher (evolve merge_daemon) + orchestrator recipe + monitoring. Test run on the pending naver backlog + a small new scrape.
- **Phase 2:** image-priority baseline, on-the-fly chain promote, scrape-coverage map overlay (`plans/scrape-coverage-map.md`).
