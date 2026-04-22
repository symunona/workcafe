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

### `01_migrate_db.py` — `just migrate`
Idempotent schema migration. Adds `clean_cafes`, `cafe_chains` tables and columns `belongs_to_cafe_id`, `name_embedding` to `scraped_cafes` and `images`. Safe to re-run — IF NOT EXISTS / column-exists checks. Does not touch scraper data.

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
