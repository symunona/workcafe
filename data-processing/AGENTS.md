# Data Processing Pipeline

Transforms raw `scraped.db` into deduplicated, enriched `clean.db`.

Run full pipeline: `just merge-pipeline`
Individual steps: `just --list` (Data Pipeline group)

## Database Architecture

| File | Written by | Read by | Contains |
|------|-----------|---------|----------|
| `data/seoul/scraped.db` | Scrapers via db_server socket | API `/api/status` metrics only | Raw scraped_cafes + images, always live |
| `data/seoul/clean.db` | Pipeline (`just merge-pipeline`) | API for all cafe/map queries | clean_cafes, cafe_chains, merged images |
| `data/seoul/englishify.db` | `05_englishify.py` | `04_normalize_pipeline.py` | Korean→English name translation cache |

**Rule:** never modify `scraped.db` during the pipeline (except `dedup-scraped` which is manual-only). Pipeline operates on a fresh copy (`clean.db`). `englishify.db` is orthogonal — accumulates translations across runs, never reset.

## Pipeline Flow

```
scraped.db
    │
    ├─[db-clean]──────────── cp scraped.db → clean.db
    │
    ├─[start-play-db]──────── db_server → /tmp/workcafe_play_db.sock → clean.db
    │
    ├─[migrate]────────────── add clean_cafes, cafe_chains tables + new columns
    │
    ├─[detect-chains]─────── name frequency + Levenshtein → cafe_chains
    │
    ├─[englishify]────────── sync names → chain pre-pass → ollama batch → englishify.db
    │
    ├─[normalize]─────────── embed + spatial merge → clean_cafes (reads englishify.db)
    │
    └─[link-images]────────── bulk set images.belongs_to_cafe_id
```

## Scripts

### `00_dedup_raw_cafes.py` — `just dedup-scraped` (MANUAL ONLY)
Removes duplicate rows from `scraped_cafes` (same provider + location, keep latest). **Mutates scraped.db directly.** Prompts for confirmation. Run before a fresh pipeline if scrapers have been running for a long time.

### `01_migrate_db.py` — `just db-migrate`
Idempotent, **additive-only** schema migration. Adds `clean_cafes`, `cafe_chains` tables and columns `belongs_to_cafe_id`, `name_embedding`, `status` (`scraped→translated→merged`), `translated_at` to `scraped_cafes`; `belongs_to_cafe_id` to `images`; `priority` to `kakao_scrape_state` (Phase 2 image queue); and the `merge_log(ts, scraped_id, clean_id, method, detail)` table (`method ∈ distance|name|chain|llm`). Backfills `status` from existing `belongs_to_cafe_id` (NOT NULL → `merged`). Safe to re-run — IF NOT EXISTS / column-exists checks. Never DROPs/overwrites scraper data.

### `02_pull_models.py` — `just pull-models`
Pulls ollama models if missing:
- `nomic-embed-text` (274 MB) — 768-dim embeddings for name similarity
- `qwen2.5:1.5b` (~1 GB) — LLM for name translation

### `03_detect_chains.py` — `just detect-chains`
Name-frequency chain detection. Algorithm:
1. Count name frequency → high-frequency = likely chain
2. Strip branch suffixes (`점`, `DT점`) and brand qualifiers (`커피`, `Coffee`, `MGC`) → brand tokens
3. Apply `KNOWN_CHAINS` dict (40+ Korean↔English pairs) for direct matching
4. Brand containment: prefix of brand_token in same script → same chain
5. Levenshtein clustering on remaining brand tokens (threshold 0.85)
6. `--llm` flag for cross-language linking of unknown pairs

Writes to `cafe_chains` with deterministic UUIDs: `uuid5("wc:chain:v1:{canonical_kr}")`.

### `04_normalize_pipeline.py` — `just normalize`
Main dedup + merge. For each scraped cafe:
1. Compute 768-dim embedding (nomic-embed-text)
2. Spatial bbox + Haversine to find nearby candidates in `clean_cafes` (within 50 m)
3. Score: Levenshtein + cosine embedding similarity
4. Merge into existing or create new `clean_cafes` row
5. Set `scraped_cafes.belongs_to_cafe_id` + `chain_id`
6. Populate `english_name` from `englishify.db` lookup (no LLM calls here)

`clean_cafes` UUIDs: `uuid5("wc:cafe:v1:{kakao_source_id}")` — stable across re-runs.

Args: `--socket`, `--englishify-db`, `--limit`, `--embed`, `--provider`.

### `05_englishify.py` — `just englishify`
Builds/updates `englishify.db` translation cache. Steps:
1. `sync_names`: INSERT OR IGNORE all distinct names from `scraped_cafes` → `name_translations`
2. `chain_prepass`: fill `english_name` from `cafe_chains.name_english` (free, no LLM)
3. `ollama_batch`: batch-30 translation for remaining NULLs via `qwen2.5:1.5b`

Safe to re-run: idempotent. Translations accumulate — never wiped between pipeline runs.

Note on model choice: `opus-mt-ko-en` is ~5× faster but hallucinates on Korean brand names (e.g. `카페드리옹` → "I'm sorry, I'm sorry"). `qwen2.5:1.5b` understands "cafe brand name" context.

### `06_update_image_links.py` — `just link-images`
Bulk sets `images.belongs_to_cafe_id` by joining against `scraped_cafes.belongs_to_cafe_id`. Faster as a separate pass than per-cafe inside normalize.

### `pipeline_daemon.py` — `workcafe-pipeline` systemd unit (Phase 1 unified pipeline)
One config-driven process (reads `data/pipeline.json` via `--config`) that replaces the manual `merge-pipeline` for streaming. Polling loop, **Order B (translate THEN merge)**, LLM forced onto **CPU** (`num_gpu:0`) so the GPU stays free for the RAM++ tagger. Per cycle, against the play DB (`clean.db`):
1. **sync** new `scraped.db` rows → `clean.db` (00). New rows arrive `status='scraped'`.
2. **translate** (reuses 05's `open_englishify`/`sync`/prepass/ollama logic, CPU-forced): translate `status='scraped'` names lacking an `english_name` in chunks of `translate_batch×10` → `englishify.db` → flip those cafes to `status='translated'`.
3. **merge** when `≥ translate_batch` translated rows exist OR the `merge_debounce_s` timer fires: run `04_normalize --merge-log --llm-cpu` + `06_link` → merged rows become `status='merged'`; each merge writes a `merge_log` row (and `03_detect_chains` runs every Nth cycle).

Reuses 00/03/04/05/06 — does not rewrite them. Start/stop/status via `just scraper-start | scraper-stop | scraper-status`. Orchestrate a new region with `just scrape-and-process-full-pipeline [region]`. Monitor: `journalctl --user -u workcafe-pipeline -f`.

> **Phase 2 (deferred):** image-priority baseline (`kakao_scrape_state.priority` column already added), on-the-fly chain promote (`chain_promote_min`), scrape-coverage map overlay.

## Key Shared Modules

| File | Purpose |
|------|---------|
| `cafe_norm_utils.py` | Haversine, embeddings, cosine sim, Levenshtein, `strip_branch()`, `llm_generate()`, chain heuristics |
| `pipeline_telemetry.py` | Timing/throughput instrumentation |
| `db_clean.py` | Resets `belongs_to_cafe_id` for full re-run (orphan cleanup) |

## DB Sockets

| Socket | Points to | Used by |
|--------|-----------|---------|
| `/tmp/workcafe_db.sock` | `scraped.db` | Scrapers (live, always running) |
| `/tmp/workcafe_play_db.sock` | `clean.db` | Pipeline scripts during merge-pipeline |

## `englishify.db` Schema

```sql
CREATE TABLE name_translations (
    korean_name   TEXT PRIMARY KEY,
    english_name  TEXT,
    model         TEXT,       -- 'chain_lookup' | 'qwen2.5:1.5b' | 'qwen2.5:1.5b-single'
    translated_at TEXT
);
```

Used by `04_normalize_pipeline.py` as a lookup dict at startup: `{korean_name: english_name}`. Never modified during normalization — only updated by `05_englishify.py`.


# Image Pipelines

Start work from `clean.db`.
Copy over ~ 100 sampled clean cafes (consistent, e.g first 100) - with their image refs to a new db - put it in the `data/seoul/history/` folder so it's instantly usable for human for review on the UI as a snapshot - always work into that.

**Snapshot export tool:** `scripts/create_tag_snapshot.py`
```bash
# Create subset of 100 cafes (top by image count) as a snapshot DB
SNAPSHOT=$(python scripts/create_tag_snapshot.py --n 100 | tail -1)
# Or full dataset:
SNAPSHOT=$(python scripts/create_tag_snapshot.py --n all | tail -1)
# Custom output path:
python scripts/create_tag_snapshot.py --n 100 --output data/seoul/history/clean_yolo_n100.db
# From a different source DB:
python scripts/create_tag_snapshot.py --n 100 --from-db data/seoul/clean.db
```
Copies: `clean_cafes`, `cafe_chains`, `scraped_cafes`, `images`, creates empty `image_tags` (with `boxes TEXT` column). Writes a `.md` file beside the DB for snapshot browser notes.

**Full pipeline for a tagging run:**
```bash
# 1. Create snapshot
SNAPSHOT=$(python scripts/create_tag_snapshot.py --n 100 | tail -1)

# 2. Tag images (YOLOv8 OIV7)
python scripts/tag_images_yolo.py --from-db "$SNAPSHOT" --conf 0.25

# 3. Roll up image tags → clean_cafes.tags (makes tags visible in map filter)
python scripts/tag_cafes_rollup.py --db "$SNAPSHOT"
```

Or in one shot: `just tag-images-yolo 100 0.25`

Always make scripts parametric, so they can be ran:
- on a subset of data - always start with the export first using `create_tag_snapshot.py` - e.g. 100 cafes limit. (always copy the belonging scraped cafe data and chains over too!)
- from a specific db (`--from-db`)
- to a specific db (use snapshot export)

Do spot check the result, and eval if it worked nicely.
Always create benchmarks, per image process speed, most popular tags, whatever makes sense, write into db's md file, so frontend I can look at it. Do not use markdown tables in the .md — they don't render in the snapshot browser. Use bullet lists instead.

After tagging, always run rollup so tags show up in filter:
```bash
python scripts/tag_cafes_rollup.py --db "$SNAPSHOT"
```
This writes `{tag: image_count}` JSON to `clean_cafes.tags` for every cafe that has tagged images.

